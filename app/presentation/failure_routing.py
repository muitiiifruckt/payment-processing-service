import logging
from typing import Any

from faststream.rabbit import RabbitBroker

log = logging.getLogger(__name__)

TRANSIENT = "transient"
PERMANENT = "permanent"


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
