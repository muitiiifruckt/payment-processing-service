from typing import Any, Protocol
from uuid import UUID

from app.application.policy import WEBHOOK_FIRST_PASS_ATTEMPTS, webhook_backoff
from app.application.ports import Clock
from app.domain.payment import Payment


class WebhookRejectedError(Exception):
    """Получатель осознанно отверг тело: повтор ничего не изменит."""


class WebhookUnavailableError(Exception):
    """Получатель временно недоступен — попытку имеет смысл повторить."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class WebhookSender(Protocol):
    async def send(self, url: str, payload: dict[str, Any]) -> None: ...


def webhook_payload(payment: Payment, *, event_id: UUID) -> dict[str, Any]:
    assert payment.processed_at is not None  # webhook уходит только по обработанному
    return {
        "event_id": str(event_id),
        "payment_id": str(payment.payment_id),
        "status": payment.status.value,
        "amount": payment.amount.formatted,
        "currency": payment.amount.currency.value,
        "processed_at": payment.processed_at.isoformat(),
    }


async def deliver_webhook(
    payment: Payment,
    *,
    event_id: UUID,
    sender: WebhookSender,
    clock: Clock,
    attempts: int = WEBHOOK_FIRST_PASS_ATTEMPTS,
) -> bool:
    payload = webhook_payload(payment, event_id=event_id)
    assert payment.webhook_url is not None
    for attempt in range(1, attempts + 1):
        try:
            await sender.send(payment.webhook_url, payload)
        except WebhookUnavailableError:
            if attempt == attempts:
                return False
            await clock.sleep(webhook_backoff(attempt))
        else:
            return True
    return False
