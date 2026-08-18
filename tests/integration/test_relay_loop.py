import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.publish_outbox import publish_pending, relay_forever
from app.infrastructure.broker.broker import make_broker
from app.infrastructure.db.models import OutboxRow
from app.infrastructure.db.outbox_repository import OutboxRepository

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
DEAD_BROKER = "amqp://guest:guest@127.0.0.1:5673/"


class TickingClock:
    """Спит без реального ожидания и считает вызовы: цикл relay должен
    крутиться, а тест — не зависеть от настоящего времени."""

    def __init__(self) -> None:
        self._now = NOW
        self.sleeps = 0

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self._now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[UUID] = []

    async def publish(self, event_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        self.published.append(event_id)


class BrokenOnceSessions:
    """Первая сессия тика разваливается — так выглядит обрыв соединения с БД."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self.calls = 0

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[AsyncSession]:
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("соединение с БД потеряно")
        async with self._sessions() as session:
            yield session


async def add_event(session: AsyncSession, event_id: UUID) -> None:
    await OutboxRepository(session).add(
        event_id=event_id,
        aggregate_id=uuid4(),
        event_type="payment.created",
        payload={"event_id": str(event_id)},
        now=NOW,
    )


async def until(predicate: Any, *, timeout: float = 5.0) -> None:
    """Поллинг с таймаутом: фоновой задаче нужен реальный обмен с БД,
    одним лишь sleep(0) до неё очередь не дойдёт."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("условие так и не выполнилось")


async def test_relay_survives_a_failed_tick_and_publishes_on_the_next(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_id = uuid4()
    async with session_factory() as session, session.begin():
        await add_event(session, event_id)

    publisher = RecordingPublisher()
    sessions = BrokenOnceSessions(session_factory)
    task = asyncio.create_task(relay_forever(sessions, publisher, TickingClock()))  # type: ignore[arg-type]
    try:
        await until(lambda: publisher.published == [event_id])
    finally:
        task.cancel()

    assert sessions.calls >= 2


async def test_relay_stops_on_cancellation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    task = asyncio.create_task(relay_forever(session_factory, RecordingPublisher(), TickingClock()))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def test_overlapping_ticks_do_not_publish_the_same_event_twice(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_id = uuid4()
    async with session_factory() as session, session.begin():
        await add_event(session, event_id)

    publisher = RecordingPublisher()

    async def tick() -> int:
        async with session_factory() as session, session.begin():
            return await publish_pending(session, publisher, TickingClock())

    counts = await asyncio.gather(tick(), tick())

    assert publisher.published == [event_id]
    assert sorted(counts) == [0, 1]


async def test_published_rows_are_kept_not_deleted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_id = uuid4()
    async with session_factory() as session, session.begin():
        await add_event(session, event_id)
        await publish_pending(session, RecordingPublisher(), TickingClock())

    async with session_factory() as session:
        row = await session.scalar(select(OutboxRow).where(OutboxRow.event_id == event_id))
    assert row is not None
    assert row.published_at is not None


async def test_broker_keeps_reconnecting_instead_of_dying_when_rabbit_is_unavailable() -> None:
    broker = make_broker(DEAD_BROKER)
    connecting = asyncio.create_task(broker.connect())
    try:
        done, _ = await asyncio.wait({connecting}, timeout=3)
        # подключение не удалось и не удастся, но это попытки, а не отказ:
        # процесс consumer'а обязан пережить недоступный брокер
        assert not done
    finally:
        connecting.cancel()
        await asyncio.gather(connecting, return_exceptions=True)
        await broker.stop()
