from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.domain.errors import InvalidTransitionError
from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.domain.status import PaymentStatus

PAYMENT_ID = UUID("0192f3a4-0000-7000-8000-000000000001")
CREATED_AT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
PROCESSED_AT = datetime(2026, 1, 1, 12, 0, 3, tzinfo=UTC)


def pending_payment() -> Payment:
    return Payment(
        payment_id=PAYMENT_ID,
        amount=Money(Decimal("100.00"), Currency.RUB),
        created_at=CREATED_AT,
    )


def test_new_payment_is_pending_and_not_processed() -> None:
    payment = pending_payment()

    assert payment.status is PaymentStatus.PENDING
    assert payment.processed_at is None


def test_marking_succeeded_sets_status_and_processed_at() -> None:
    payment = pending_payment()

    payment.mark_succeeded(now=PROCESSED_AT)

    assert payment.status is PaymentStatus.SUCCEEDED
    assert payment.processed_at == PROCESSED_AT


def test_marking_failed_sets_status_and_processed_at() -> None:
    payment = pending_payment()

    payment.mark_failed(now=PROCESSED_AT)

    assert payment.status is PaymentStatus.FAILED
    assert payment.processed_at == PROCESSED_AT


def test_transition_from_terminal_status_is_rejected() -> None:
    payment = pending_payment()
    payment.mark_succeeded(now=PROCESSED_AT)

    with pytest.raises(InvalidTransitionError):
        payment.mark_failed(now=PROCESSED_AT)


def test_repeating_the_same_terminal_transition_is_rejected() -> None:
    payment = pending_payment()
    payment.mark_succeeded(now=PROCESSED_AT)

    with pytest.raises(InvalidTransitionError):
        payment.mark_succeeded(now=PROCESSED_AT)
