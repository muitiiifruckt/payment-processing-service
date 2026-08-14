from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.domain.status import PaymentStatus
from app.infrastructure.db.models import PaymentRow


def _to_domain(row: PaymentRow) -> Payment:
    return Payment(
        payment_id=row.payment_id,
        amount=Money(row.amount, Currency(row.currency)),
        created_at=row.created_at,
        idempotency_key=row.idempotency_key,
        request_hash=row.request_hash,
        status=PaymentStatus(row.status),
        processed_at=row.processed_at,
    )


def _to_row(payment: Payment) -> PaymentRow:
    return PaymentRow(
        payment_id=payment.payment_id,
        amount=payment.amount.amount,
        currency=payment.amount.currency.value,
        status=payment.status.value,
        idempotency_key=payment.idempotency_key,
        request_hash=payment.request_hash,
        created_at=payment.created_at,
        processed_at=payment.processed_at,
    )


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, payment: Payment) -> None:
        self._session.add(_to_row(payment))

    async def get(self, payment_id: UUID) -> Payment | None:
        row = await self._session.scalar(
            select(PaymentRow).where(PaymentRow.payment_id == payment_id)
        )
        return _to_domain(row) if row is not None else None
