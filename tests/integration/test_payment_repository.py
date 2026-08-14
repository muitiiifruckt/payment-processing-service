from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.infrastructure.db.payment_repository import PaymentRepository

PAYMENT_ID = UUID("0192f3a4-0000-7000-8000-000000000010")
CREATED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def a_payment(**overrides: object) -> Payment:
    defaults: dict[str, object] = {
        "payment_id": PAYMENT_ID,
        "amount": Money(Decimal("100.00"), Currency.RUB),
        "created_at": CREATED_AT,
        "idempotency_key": "key-1",
        "request_hash": "0" * 64,
    }
    return Payment(**(defaults | overrides))  # type: ignore[arg-type]


async def test_saved_payment_is_read_back_unchanged(session: AsyncSession) -> None:
    repository = PaymentRepository(session)
    payment = a_payment(amount=Money(Decimal("100.0001"), Currency.EUR))

    await repository.add(payment)
    await session.flush()

    assert await repository.get(PAYMENT_ID) == payment
