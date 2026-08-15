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
) -> Payment:
    """Платёж и событие пишутся одной транзакцией. В брокер отсюда не публикуем.

    При занятом ключе идемпотентности возвращает существующий платёж
    и не создаёт второго события.
    """
    payments = PaymentRepository(session)

    # Верно при READ COMMITTED: перечитывание после конфликта берёт свежий снимок.
    # Под REPEATABLE READ проигравший увидел бы снимок до чужого коммита и получил
    # бы 500 на законном повторе ключа.
    if not await payments.add_if_absent(payment):
        existing = await payments.get_by_idempotency_key(payment.idempotency_key)
        if existing is None:
            raise RuntimeError(f"ключ {payment.idempotency_key} занят, но платёж не найден")
        return existing

    await OutboxRepository(session).add(
        event_id=event_id,
        aggregate_id=payment.payment_id,
        event_type=PAYMENT_CREATED,
        payload={"event_id": str(event_id), "payment_id": str(payment.payment_id)},
        now=now,
    )
    return payment
