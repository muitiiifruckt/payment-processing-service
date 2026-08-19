from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from app.application.notify import webhook_payload
from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.domain.status import PaymentStatus

PAYMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
EVENT_ID = UUID("22222222-2222-2222-2222-222222222222")
CREATED = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
PROCESSED = datetime(2026, 1, 1, 12, 0, 5, tzinfo=UTC)


def a_payment() -> Payment:
    return Payment(
        payment_id=PAYMENT_ID,
        amount=Money(Decimal("100.5"), Currency.RUB),
        created_at=CREATED,
        idempotency_key="key",
        request_hash="0" * 64,
        status=PaymentStatus.SUCCEEDED,
        processed_at=PROCESSED,
        webhook_url="https://example.test/hook",
    )


def test_payload_carries_the_agreed_fields() -> None:
    payload = webhook_payload(a_payment(), event_id=EVENT_ID)

    assert payload == {
        "event_id": str(EVENT_ID),
        "event_type": "payment.processed",
        "payment_id": str(PAYMENT_ID),
        "status": "succeeded",
        "amount": "100.50",
        "currency": "RUB",
        "processed_at": "2026-01-01T12:00:05+00:00",
    }
