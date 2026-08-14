from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.domain.status import PaymentStatus

PAYMENT_ID = UUID("0192f3a4-0000-7000-8000-000000000001")
CREATED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_new_payment_is_pending_and_not_processed() -> None:
    payment = Payment(
        payment_id=PAYMENT_ID,
        amount=Money(Decimal("100.00"), Currency.RUB),
        created_at=CREATED_AT,
    )

    assert payment.status is PaymentStatus.PENDING
    assert payment.processed_at is None
