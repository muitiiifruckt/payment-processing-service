from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.payment import Payment
from app.infrastructure.db.outbox_repository import OutboxRepository
from app.infrastructure.db.payment_repository import PaymentRepository

PAYMENT_CREATED = "payment.created"


async def create_payment(
    session: AsyncSession,
    payment: Payment,
    *,
    event_id: UUID,
    now: datetime,
) -> None:
    """Платёж и событие пишутся одной транзакцией. В брокер отсюда не публикуем."""
    await PaymentRepository(session).add(payment)
    await OutboxRepository(session).add(
        event_id=event_id,
        aggregate_id=payment.payment_id,
        event_type=PAYMENT_CREATED,
        payload={"event_id": str(event_id), "payment_id": str(payment.payment_id)},
        now=now,
    )
