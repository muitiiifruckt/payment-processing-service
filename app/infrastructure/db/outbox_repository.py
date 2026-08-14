from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import OutboxRow


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        event_id: UUID,
        aggregate_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> None:
        self._session.add(
            OutboxRow(
                event_id=event_id,
                aggregate_id=aggregate_id,
                event_type=event_type,
                payload=payload,
                created_at=now,
                # первая попытка публикации доступна сразу
                next_attempt_at=now,
            )
        )
