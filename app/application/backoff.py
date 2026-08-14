"""Задержки повторов. Механизмы из RFC §6 независимы — константы разные."""

#: TTL очередей повторов обработки сообщения, секунды
RETRY_QUEUE_DELAYS: tuple[int, ...] = (2, 4, 8)

#: Задержка перед второй попыткой доставки webhook, секунды
WEBHOOK_BASE_DELAY = 1.0


def webhook_backoff(attempt: int) -> float:
    """Задержка перед попыткой attempt + 1."""
    return float(WEBHOOK_BASE_DELAY * 2 ** (attempt - 1))
