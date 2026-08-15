import logging
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.policy import OUTBOX_BATCH, OUTBOX_POLL_INTERVAL, OUTBOX_PUBLISH_TIMEOUT
from app.application.ports import Clock

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
    return 0


async def relay_forever(
    sessions: async_sessionmaker[AsyncSession],
    publisher: EventPublisher,
    clock: Clock,
    *,
    interval: float = OUTBOX_POLL_INTERVAL,
) -> None:
    return None
