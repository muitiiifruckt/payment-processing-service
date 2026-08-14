"""Временные константы. Механизмы из RFC §6 независимы — значения разные."""

from datetime import timedelta

#: TTL очередей повторов обработки сообщения, секунды
RETRY_QUEUE_DELAYS: tuple[int, ...] = (2, 4, 8)

#: Задержка перед второй попыткой доставки webhook, секунды
WEBHOOK_BASE_DELAY = 1.0

#: Срок удержания захвата платежа. Обязан быть меньше суммы задержек повторов,
#: иначе платёж, чей обработчик умер, не удастся захватить ни на одном повторе
#: и он уедет в DLQ, навсегда оставшись pending.
CLAIM_LEASE = timedelta(seconds=10)


def webhook_backoff(attempt: int) -> float:
    """Задержка перед попыткой attempt + 1."""
    return float(WEBHOOK_BASE_DELAY * 2 ** (attempt - 1))
