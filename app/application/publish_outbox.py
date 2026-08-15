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
) -> int:
    """Один проход. Пометка только после подтверждения брокера."""
    now = clock.now()
    outbox = OutboxRepository(session)
    events = await outbox.claim_batch(now=now, limit=batch)

    published = 0
    for event in events:
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
            if event.attempts + 1 >= OUTBOX_ALERT_AFTER:
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
        try:
            async with sessions() as session, session.begin():
                await publish_pending(session, publisher, clock)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("тик relay упал")
        await clock.sleep(interval)
