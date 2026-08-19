import logging
from typing import Any

from faststream.rabbit import RabbitBroker

from app.application.policy import MAX_BUSY_WAITS, MAX_HANDLER_RUNS
from app.infrastructure.broker import topology

log = logging.getLogger(__name__)

TRANSIENT = "transient"
PERMANENT = "permanent"
BUSY = "busy"
#: Длинный текст ошибки в заголовке раздувает сообщение и упирается в лимит кадра
MAX_REASON = 512


async def route_failure(
    broker: RabbitBroker,
    event: dict[str, Any] | bytes,
    *,
    attempt: int,
    error: Exception,
    kind: str,
    event_type: str,
    busy: int = 0,
    original: dict[str, Any] | None = None,
) -> None:
    """Отправить сообщение на повтор или в DLQ. Решение о классе отказа
    принимает вызывающий: здесь только маршрутизация."""
    log.warning("отказ обработки, прогон %d, класс %s: %s", attempt, kind, error)

    if kind == BUSY and busy < MAX_BUSY_WAITS:
        # ждём освобождения, не списывая прогон: чужой захват — не отказ
        # обработки, и платёж, с которым всё в порядке, не должен из-за
        # него исчерпать бюджет и уехать в DLQ
        await _publish(
            broker,
            event,
            routing_key=topology.retry_key(1),
            attempt=attempt,
            busy=busy + 1,
            error=error,
            kind=kind,
            event_type=event_type,
            original=original,
        )
        return

    # счётчик ожиданий обнуляется: он про одну схватку за платёж,
    # а не про всю жизнь сообщения
    next_attempt = attempt + 1
    if kind == PERMANENT or next_attempt >= MAX_HANDLER_RUNS:
        await _dead_letter(
            broker,
            event,
            attempt=attempt,
            error=error,
            kind=kind,
            event_type=event_type,
            busy=0,
            original=original,
        )
        return

    await _publish(
        broker,
        event,
        routing_key=topology.retry_key(next_attempt),
        attempt=next_attempt,
        busy=0,
        error=error,
        kind=kind,
        event_type=event_type,
        original=original,
    )


async def _publish(
    broker: RabbitBroker,
    event: dict[str, Any] | bytes,
    *,
    routing_key: str,
    attempt: int,
    busy: int,
    error: Exception,
    kind: str,
    event_type: str,
    original: dict[str, Any] | None = None,
) -> None:
    await broker.publish(
        event,
        routing_key=routing_key,
        exchange=topology.payments_exchange,
        message_id=_message_id(event),
        headers=_headers(
            event,
            attempt=attempt,
            error=error,
            kind=kind,
            event_type=event_type,
            busy=busy,
            original=original,
        ),
        persist=True,
        mandatory=True,
    )


async def _dead_letter(
    broker: RabbitBroker,
    event: dict[str, Any] | bytes,
    *,
    attempt: int,
    error: Exception,
    kind: str,
    event_type: str,
    busy: int = 0,
    original: dict[str, Any] | None = None,
) -> None:
    await broker.publish(
        event,
        routing_key=topology.DLQ_KEY,
        exchange=topology.dlx_exchange,
        message_id=_message_id(event),
        headers=_headers(
            event,
            attempt=attempt,
            error=error,
            kind=kind,
            event_type=event_type,
            busy=busy,
            original=original,
        ),
        persist=True,
        mandatory=True,
    )


def _message_id(event: dict[str, Any] | bytes) -> str | None:
    """Публикующая сторона кладёт сюда event_id. Потеряв его при пересылке,
    теряем и связь сообщения с событием после первого же повтора."""
    if isinstance(event, bytes):
        return None
    raw = event.get("event_id")
    return str(raw) if isinstance(raw, str) else None


def _headers(
    event: dict[str, Any] | bytes,
    *,
    attempt: int,
    error: Exception,
    kind: str,
    event_type: str,
    busy: int = 0,
    original: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # RFC §7.3: исходные заголовки сохраняются, к ним добавляются наши.
    # Собрать словарь с нуля значит потерять всё чужое на первом же повторе
    return {
        **{key: value for key, value in (original or {}).items() if not key.startswith("x-")},
        "x-event-type": event_type,
        "x-attempt": attempt,
        "x-busy": busy,
        "x-failure-class": kind,
        "x-failure-reason": f"{type(error).__name__}: {error}"[:MAX_REASON],
        "x-payment-id": "" if isinstance(event, bytes) else str(event.get("payment_id") or ""),
    }
