"""Временные константы. Механизмы из RFC §6 независимы — значения разные."""

from datetime import timedelta

#: TTL очередей повторов обработки сообщения, секунды
RETRY_QUEUE_DELAYS: tuple[int, ...] = (2, 4, 8)

#: Задержка перед второй попыткой доставки webhook, секунды
WEBHOOK_BASE_DELAY = 1.0
#: Первичная отправка плюс два повтора (RFC §6.2)
WEBHOOK_FIRST_PASS_ATTEMPTS = 3
#: Потолок для Retry-After: без него получатель подвешивает обработчик
#: на произвольное время и срывает подтверждение сообщения
WEBHOOK_MAX_RETRY_AFTER = 30.0

#: Срок удержания захвата платежа. Обязан быть меньше суммы задержек повторов,
#: иначе платёж, чей обработчик умер, не удастся захватить ни на одном повторе
#: и он уедет в DLQ, навсегда оставшись pending.
CLAIM_LEASE = timedelta(seconds=10)


#: Публикация из outbox: период тика, размер пачки, таймаут ожидания подтверждения
OUTBOX_POLL_INTERVAL = 1.0
OUTBOX_BATCH = 50
OUTBOX_PUBLISH_TIMEOUT = 5.0
#: Потолок длительности тика. Пачка захвачена FOR UPDATE, поэтому без него
#: медленный брокер держит строки и соединение batch * timeout секунд подряд.
OUTBOX_TICK_BUDGET = 15.0
OUTBOX_BASE_DELAY = 1.0
OUTBOX_MAX_DELAY = 60.0
#: После стольких неудач подряд событие логируется как требующее внимания
OUTBOX_ALERT_AFTER = 10


def webhook_backoff(attempt: int) -> float:
    """Задержка перед попыткой attempt + 1."""
    return float(WEBHOOK_BASE_DELAY * 2 ** (attempt - 1))


def outbox_backoff(attempts: int) -> float:
    """Задержка перед следующей попыткой публикации, с потолком."""
    return float(min(OUTBOX_BASE_DELAY * 2**attempts, OUTBOX_MAX_DELAY))
