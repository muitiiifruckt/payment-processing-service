from typing import Any

from faststream.rabbit import Channel, RabbitBroker

from app.infrastructure.broker import topology
from app.infrastructure.config import settings


def make_broker(url: str | None = None) -> RabbitBroker:
    return RabbitBroker(
        url or settings.rabbitmq_url,
        # RFC §5.2: процесс обязан подняться и при мёртвом брокере. С fail_fast
        # первая же неудачная попытка убивает старт, и в compose consumer
        # не переживает загрузку RabbitMQ
        fail_fast=False,
        default_channel=Channel(
            prefetch_count=settings.prefetch_count,
            # без подтверждения publish возвращается сразу после записи в сокет,
            # и outbox пометит потерянное сообщение доставленным
            publisher_confirms=True,
            # mandatory без этого молча проглатывает возврат немаршрутизируемого
            on_return_raises=True,
        ),
    )


async def declare_topology(broker: RabbitBroker) -> None:
    """У retry-очередей и DLQ нет подписчиков — фреймворк их не создаст
    и не привяжет, а публикация в непривязанную очередь теряется."""
    exchanges = {
        exchange.name: await broker.declare_exchange(exchange)
        for exchange in topology.ALL_EXCHANGES
    }
    for queue, exchange in topology.BINDINGS:
        declared = await broker.declare_queue(queue)
        await declared.bind(exchanges[exchange.name], routing_key=queue.routing_key)


class RabbitEventPublisher:
    def __init__(self, broker: RabbitBroker) -> None:
        self._broker = broker

    async def publish(self, event_id: Any, event_type: str, payload: dict[str, Any]) -> None:
        await self._broker.publish(
            payload,
            routing_key=topology.NEW_KEY,
            exchange=topology.payments_exchange,
            message_id=str(event_id),
            headers={"x-event-type": event_type, "x-attempt": 0},
            persist=True,
            mandatory=True,
        )
