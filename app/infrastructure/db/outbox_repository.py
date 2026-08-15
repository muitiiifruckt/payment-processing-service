from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import OutboxRow


@dataclass(frozen=True, slots=True)
class PendingEvent:
    event_id: UUID
    event_type: str
    payload: dict[str, Any]
    attempts: int


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
                next_attempt_at=now,
            )
        )

    async def claim_batch(self, *, now: datetime, limit: int) -> list[PendingEvent]:
        """Неопубликованные события, чей срок наступил.

        Блокировка с пропуском занятых защищает от перекрытия соседних тиков
        и от повторного запуска после рестарта.
        """
        statement = (
            select(OutboxRow)
            .where(OutboxRow.published_at.is_(None), OutboxRow.next_attempt_at <= now)
            .order_by(OutboxRow.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = (await self._session.scalars(statement)).all()
        return [
            PendingEvent(
                event_id=row.event_id,
                event_type=row.event_type,
                payload=row.payload,
                attempts=row.attempts,
            )
            for row in rows
        ]

    async def mark_published(self, event_id: UUID, *, now: datetime) -> None:
        """Только после подтверждения от брокера."""
        await self._session.execute(
            update(OutboxRow)
            .where(OutboxRow.event_id == event_id)
            .values(published_at=now, last_error=None)
        )

    async def defer(self, event_id: UUID, *, next_attempt_at: datetime, error: str) -> None:
        """Публикация не удалась — отодвинуть и запомнить причину."""
        await self._session.execute(
            update(OutboxRow)
            .where(OutboxRow.event_id == event_id)
            .values(
                next_attempt_at=next_attempt_at,
                attempts=OutboxRow.attempts + 1,
                last_error=error[:1000],
            )
        )
