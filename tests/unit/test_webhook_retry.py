from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.application.notify import (
    WebhookRejectedError,
    WebhookUnavailableError,
    deliver_webhook,
)
from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.domain.status import PaymentStatus

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
URL = "https://example.test/hook"


class RecordingClock:
    def __init__(self) -> None:
        self._now = NOW
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self._now += timedelta(seconds=seconds)


class Sender:
    """Отдаёт заготовленные исходы по одному на попытку."""

    def __init__(self, *outcomes: Exception | None) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def send(self, url: str, payload: dict[str, Any]) -> None:
        self.calls.append((url, payload))
        outcome = self._outcomes[min(len(self.calls), len(self._outcomes)) - 1]
        if outcome is not None:
            raise outcome


def a_payment(payment_id: UUID | None = None) -> Payment:
    return Payment(
        payment_id=payment_id or uuid4(),
        amount=Money(Decimal("100.00"), Currency.RUB),
        created_at=NOW,
        idempotency_key="key",
        request_hash="0" * 64,
        status=PaymentStatus.SUCCEEDED,
        processed_at=NOW,
        webhook_url=URL,
    )


async def test_first_pass_makes_three_attempts_with_growing_delays() -> None:
    sender = Sender(WebhookUnavailableError("503"))
    clock = RecordingClock()

    delivered = await deliver_webhook(a_payment(), event_id=uuid4(), sender=sender, clock=clock)

    assert delivered is False
    assert len(sender.calls) == 3
    assert clock.slept == [1.0, 2.0]

