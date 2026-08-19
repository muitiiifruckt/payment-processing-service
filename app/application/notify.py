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

#: Тип уведомления в теле webhook. Не совпадает с типом события в очереди:
#: там payment.created, здесь сообщается уже свершившийся исход
PAYMENT_PROCESSED = "payment.processed"


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
    if payment.processed_at is None:
        # не assert: под -O проверка исчезнет, и вместо внятной ошибки
        # получится AttributeError на None
        raise ValueError(f"платёж {payment.payment_id} ещё не обработан")
    return {
        "event_id": str(event_id),
        # RFC §9: получатель должен различать типы уведомлений, не гадая
        # по составу полей
        "event_type": PAYMENT_PROCESSED,
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
                raise WebhookNotDeliveredError(
                    f"получатель не подтвердил за {attempts} попыт(ку/ки/ок): {error}"
                ) from error
            # получатель сам назвал срок — он знает о своей загрузке больше,
            # но не настолько, чтобы подвешивать обработчик на произвольное время
            # своя задержка — пол, потолок — защита от произвольного срока:
            # Retry-After: 0 от нагруженного получателя иначе снимает паузу вовсе
            own = webhook_backoff(attempt)
            delay = (
                min(max(error.retry_after, own), WEBHOOK_MAX_RETRY_AFTER)
                if error.retry_after is not None
                else own
            )
            await clock.sleep(delay)
        else:
            return True
    raise WebhookNotDeliveredError(f"получатель не подтвердил за {attempts} попыт(ку/ки/ок)")
