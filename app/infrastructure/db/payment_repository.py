from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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


def _to_values(payment: Payment) -> dict[str, object]:
    return {
        "payment_id": payment.payment_id,
        "amount": payment.amount.amount,
        "currency": payment.amount.currency.value,
        "status": payment.status.value,
        "idempotency_key": payment.idempotency_key,
        "request_hash": payment.request_hash,
        "created_at": payment.created_at,
        "processed_at": payment.processed_at,
    }


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_if_absent(self, payment: Payment) -> bool:
        """False, если ключ идемпотентности уже занят.

        Через ON CONFLICT, а не перехват IntegrityError: после нарушения
        уникальности транзакция в PostgreSQL непригодна, и перечитать в ней
        существующий платёж уже нельзя.
        """
        statement = (
            pg_insert(PaymentRow)
            .values(**_to_values(payment))
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(PaymentRow.payment_id)
        )
        return await self._session.scalar(statement) is not None

    async def claim(self, payment_id: UUID, *, now: datetime, lease: timedelta) -> bool:
        """Взять платёж в работу. False, если он занят или уже терминален."""
        return False

    async def release(self, payment_id: UUID) -> None:
        """Снять захват — обработчик закончил, удачно или нет."""

    async def get(self, payment_id: UUID) -> Payment | None:
        row = await self._session.scalar(
            select(PaymentRow).where(PaymentRow.payment_id == payment_id)
        )
        return _to_domain(row) if row is not None else None

    async def get_by_idempotency_key(self, key: str) -> Payment | None:
        row = await self._session.scalar(
            select(PaymentRow).where(PaymentRow.idempotency_key == key)
        )
        return _to_domain(row) if row is not None else None
