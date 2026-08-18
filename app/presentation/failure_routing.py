import logging
from typing import Any

from faststream.rabbit import RabbitBroker

from app.application.policy import MAX_HANDLER_RUNS
from app.infrastructure.broker import topology

log = logging.getLogger(__name__)

TRANSIENT = "transient"
PERMANENT = "permanent"
#: Длинный текст ошибки в заголовке раздувает сообщение и упирается в лимит кадра
MAX_REASON = 512


async def route_failure(
    broker: RabbitBroker,
    event: dict[str, Any],
    *,
    attempt: int,
    error: Exception,
    kind: str,
) -> None:
    """Отправить сообщение на повтор или в DLQ. Решение о классе отказа
    принимает вызывающий: здесь только маршрутизация."""
    log.warning("отказ обработки, прогон %d, класс %s: %s", attempt, kind, error)
    next_attempt = attempt + 1
    if kind == PERMANENT or next_attempt >= MAX_HANDLER_RUNS:
        await _dead_letter(broker, event, attempt=attempt, error=error, kind=kind)
        return

    await broker.publish(
        event,
        routing_key=topology.retry_key(next_attempt),
        exchange=topology.payments_exchange,
        headers=_headers(event, attempt=next_attempt, error=error, kind=kind),
        persist=True,
        mandatory=True,
    )


async def _dead_letter(
    broker: RabbitBroker,
    event: dict[str, Any],
    *,
    attempt: int,
    error: Exception,
    kind: str,
) -> None:
    await broker.publish(
        event,
        routing_key=topology.DLQ_KEY,
        exchange=topology.dlx_exchange,
        headers=_headers(event, attempt=attempt, error=error, kind=kind),
        persist=True,
        mandatory=True,
    )


def _headers(event: dict[str, Any], *, attempt: int, error: Exception, kind: str) -> dict[str, Any]:
    return {
        "x-event-type": event.get("event_type", "payment.created"),
        "x-attempt": attempt,
        "x-failure-class": kind,
        "x-failure-reason": f"{type(error).__name__}: {error}"[:MAX_REASON],
        "x-payment-id": str(event.get("payment_id", "")),
    }
