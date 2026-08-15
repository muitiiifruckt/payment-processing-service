import asyncio
import logging
from datetime import timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.policy import (
    OUTBOX_ALERT_AFTER,
    OUTBOX_BATCH,
    OUTBOX_POLL_INTERVAL,
    OUTBOX_PUBLISH_TIMEOUT,
    OUTBOX_TICK_BUDGET,
    outbox_backoff,
)
from app.application.ports import Clock
from app.infrastructure.db.outbox_repository import OutboxRepository

log = logging.getLogger(__name__)


class EventPublisher(Protocol):
    async def publish(self, event_id: UUID, event_type: str, payload: dict[str, Any]) -> None: ...


async def publish_pending(
    session: AsyncSession,
    publisher: EventPublisher,
    clock: Clock,
    *,
    batch: int = OUTBOX_BATCH,
    timeout: float = OUTBOX_PUBLISH_TIMEOUT,
    budget: float = OUTBOX_TICK_BUDGET,
) -> int:
    """Один проход. Пометка только после подтверждения брокера."""
    now = clock.now()
    outbox = OutboxRepository(session)
    events = await outbox.claim_batch(now=now, limit=batch)

    deadline = asyncio.get_running_loop().time() + budget
    published = 0
    for index, event in enumerate(events):
        if asyncio.get_running_loop().time() >= deadline:
            # незатронутые события просто не помечены: блокировка отпустится
            # с транзакцией, следующий тик заберёт их снова
            log.warning("тик relay прерван по бюджету, осталось событий: %d", len(events) - index)
            break
        try:
            await asyncio.wait_for(
                publisher.publish(event.event_id, event.event_type, event.payload),
                timeout=timeout,
            )
        except Exception as error:
            delay = outbox_backoff(event.attempts)
            await outbox.defer(
                event.event_id,
                next_attempt_at=now + timedelta(seconds=delay),
                error=f"{type(error).__name__}: {error}",
            )
            # ровно на пороге, иначе застрявшее событие пишет ошибку раз в минуту вечно
            if event.attempts + 1 == OUTBOX_ALERT_AFTER:
                log.error(
                    "событие %s не публикуется %d раз подряд", event.event_id, event.attempts + 1
                )
        else:
            await outbox.mark_published(event.event_id, now=now)
            published += 1

    return published


async def relay_forever(
    sessions: async_sessionmaker[AsyncSession],
    publisher: EventPublisher,
    clock: Clock,
    *,
    interval: float = OUTBOX_POLL_INTERVAL,
) -> None:
    """Фоновая задача consumer'а. Ошибка тика не должна её убивать —
    молча умерший relay перестаёт разгружать outbox незаметно."""
    while True:
        published = 0
        try:
            async with sessions() as session, session.begin():
                published = await publish_pending(session, publisher, clock)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("тик relay упал")
        # пачка ушла целиком — очередь наверняка не пуста, ждать нечего:
        # иначе накопленный за простой брокера завал разгружается по batch в секунду
        if published < OUTBOX_BATCH:
            await clock.sleep(interval)
