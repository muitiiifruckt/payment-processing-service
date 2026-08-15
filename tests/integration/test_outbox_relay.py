import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.policy import outbox_backoff
from app.application.publish_outbox import publish_pending
from app.infrastructure.db.models import OutboxRow
from app.infrastructure.db.outbox_repository import OutboxRepository

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class FrozenClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[UUID, str, dict[str, Any]]] = []

    async def publish(self, event_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        self.published.append((event_id, event_type, payload))


class RefusingPublisher:
    def __init__(self, reason: str = "брокер недоступен") -> None:
        self.reason = reason
        self.calls = 0

    async def publish(self, event_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
        self.calls += 1
        raise ConnectionError(self.reason)


async def add_event(session: AsyncSession, event_id: UUID) -> None:
    await OutboxRepository(session).add(
        event_id=event_id,
        aggregate_id=uuid4(),
        event_type="payment.created",
        payload={"event_id": str(event_id)},
        now=NOW,
    )
    await session.flush()


async def row(session: AsyncSession, event_id: UUID) -> OutboxRow:
    found = await session.scalar(select(OutboxRow).where(OutboxRow.event_id == event_id))
    assert found is not None
    return found


async def test_published_event_is_marked_and_not_published_again(session: AsyncSession) -> None:
    event_id = uuid4()
    await add_event(session, event_id)
    publisher = RecordingPublisher()

    await publish_pending(session, publisher, FrozenClock())
    await publish_pending(session, publisher, FrozenClock())

    assert [event[0] for event in publisher.published] == [event_id]
    assert (await row(session, event_id)).published_at == NOW


async def test_event_stays_unpublished_when_the_broker_refuses(session: AsyncSession) -> None:
    event_id = uuid4()
    await add_event(session, event_id)
    publisher = RefusingPublisher()

    published = await publish_pending(session, publisher, FrozenClock())

    assert publisher.calls == 1
    assert published == 0
    assert (await row(session, event_id)).published_at is None


async def test_failed_publish_defers_and_records_the_reason(session: AsyncSession) -> None:
    event_id = uuid4()
    await add_event(session, event_id)

    await publish_pending(session, RefusingPublisher("канал закрыт"), FrozenClock())

    stored = await row(session, event_id)
    assert stored.attempts == 1
    assert stored.next_attempt_at == NOW + timedelta(seconds=outbox_backoff(0))
    assert "канал закрыт" in (stored.last_error or "")


async def test_event_survives_the_broker_outage_and_goes_out_later(
    session: AsyncSession,
) -> None:
    event_id = uuid4()
    await add_event(session, event_id)

    await publish_pending(session, RefusingPublisher(), FrozenClock())

    recovered = RecordingPublisher()
    later = FrozenClock(NOW + timedelta(minutes=5))
    await publish_pending(session, recovered, later)

    assert [event[0] for event in recovered.published] == [event_id]
    assert (await row(session, event_id)).published_at == later.now()


async def test_event_id_survives_republishing(session: AsyncSession) -> None:
    event_id = uuid4()
    await add_event(session, event_id)

    await publish_pending(session, RefusingPublisher(), FrozenClock())
    recovered = RecordingPublisher()
    await publish_pending(session, recovered, FrozenClock(NOW + timedelta(minutes=5)))

    assert recovered.published[0][0] == event_id
    assert recovered.published[0][2]["event_id"] == str(event_id)


async def test_publish_that_hangs_is_cut_off_by_the_timeout(session: AsyncSession) -> None:
    class HangingPublisher:
        async def publish(self, event_id: UUID, event_type: str, payload: dict[str, Any]) -> None:
            await asyncio.Event().wait()

    event_id = uuid4()
    await add_event(session, event_id)

    await publish_pending(session, HangingPublisher(), FrozenClock(), timeout=0.05)

    stored = await row(session, event_id)
    assert stored.published_at is None
    assert stored.attempts == 1
