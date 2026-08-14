"""Приём платежа.

Транзакционная граница outbox: платёж и событие пишутся одной транзакцией,
одним коммитом (RFC §5.1). Публикация в брокер сюда не входит вовсе — иначе
недоступность брокера роняла бы приём платежа, а весь смысл паттерна в том,
чтобы не роняла.
"""

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
    await PaymentRepository(session).add(payment)
    # Событие несёт только идентификаторы: остальное обработчик читает из БД,
    # иначе появился бы второй источник правды, расходящийся при повторной
    # доставке (RFC §9)
    await OutboxRepository(session).add(
        event_id=event_id,
        aggregate_id=payment.payment_id,
        event_type=PAYMENT_CREATED,
        payload={"event_id": str(event_id), "payment_id": str(payment.payment_id)},
        now=now,
    )
