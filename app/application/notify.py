import logging
from typing import Any, Protocol
from uuid import UUID

from app.application.policy import WEBHOOK_FIRST_PASS_ATTEMPTS, webhook_backoff
from app.application.ports import Clock
from app.domain.payment import Payment

log = logging.getLogger(__name__)


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
        except WebhookRejectedError:
            # получатель осознанно отверг тело: повтор ничего не изменит
            log.warning("получатель отверг webhook по платежу %s", payment.payment_id)
            return False
        except WebhookUnavailableError as error:
            if attempt == attempts:
                return False
            # получатель сам назвал срок — он знает о своей загрузке больше
            delay = error.retry_after if error.retry_after is not None else webhook_backoff(attempt)
            await clock.sleep(delay)
        else:
            return True
    return False
