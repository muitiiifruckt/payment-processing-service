from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.infrastructure.db.payment_repository import PaymentRepository

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
LEASE = timedelta(seconds=10)


async def stored_payment(session: AsyncSession, payment_id: UUID, key: str) -> Payment:
    payment = Payment(
        payment_id=payment_id,
        amount=Money(Decimal("100.00"), Currency.RUB),
        created_at=NOW,
        idempotency_key=key,
        request_hash="0" * 64,
    )
    await PaymentRepository(session).add_if_absent(payment)
    return payment


async def test_claiming_a_pending_payment_succeeds(session: AsyncSession) -> None:
    payment_id = UUID("0192f3a4-0000-7000-8000-000000000030")
    await stored_payment(session, payment_id, "key-claim-1")
    repository = PaymentRepository(session)

    assert await repository.claim(payment_id, now=NOW, lease=LEASE)
