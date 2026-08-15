from typing import Any

from faststream.rabbit import RabbitBroker

from app.infrastructure.broker import topology
from app.infrastructure.config import settings


def make_broker(url: str | None = None) -> RabbitBroker:
    return RabbitBroker(url or settings.rabbitmq_url)


async def declare_topology(broker: RabbitBroker) -> None:
    return None


class RabbitEventPublisher:
    def __init__(self, broker: RabbitBroker) -> None:
        self._broker = broker

    async def publish(self, event_id: Any, event_type: str, payload: dict[str, Any]) -> None:
        await self._broker.publish(payload, routing_key=topology.NEW_KEY)
