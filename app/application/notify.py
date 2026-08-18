import logging
from typing import Any, Protocol
from uuid import UUID

from app.application.policy import (
    WEBHOOK_FIRST_PASS_ATTEMPTS,
    WEBHOOK_MAX_RETRY_AFTER,
    webhook_backoff,
)
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


class WebhookNotDeliveredError(Exception):
    """Попытки исчерпаны, получатель так и не подтвердил. По RFC §6.2 это
    временный отказ обработки: сообщение уходит в повтор."""


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
    if payment.webhook_url is None:
        # RFC §8.2: платёж без адреса считается уведомлённым
        return True

    payload = webhook_payload(payment, event_id=event_id)
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
            # получатель сам назвал срок — он знает о своей загрузке больше,
            # но не настолько, чтобы подвешивать обработчик на произвольное время
            delay = (
                min(error.retry_after, WEBHOOK_MAX_RETRY_AFTER)
                if error.retry_after is not None
                else webhook_backoff(attempt)
            )
            await clock.sleep(delay)
        else:
            return True
    return False
