from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import aio_pika
import pytest
from faststream.rabbit import RabbitBroker

from app.infrastructure.broker import topology
from app.infrastructure.broker.broker import RabbitEventPublisher, declare_topology, make_broker


@pytest.fixture
async def broker(rabbitmq_url: str) -> AsyncIterator[RabbitBroker]:
    broker = make_broker(rabbitmq_url)
    await broker.connect()
    await declare_topology(broker)
    yield broker
    await broker.stop()


async def take(rabbitmq_url: str, queue_name: str) -> aio_pika.abc.AbstractIncomingMessage | None:
    """Одно сообщение отдельным соединением: подписка FastStream здесь лишняя."""
    connection = await aio_pika.connect_robust(rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        queue = await channel.get_queue(queue_name, ensure=False)
        message = await queue.get(fail=False, timeout=5)
        if message is not None:
            await message.ack()
        return message


async def publish_event(broker: RabbitBroker, event_id: UUID) -> None:
    await RabbitEventPublisher(broker).publish(event_id, "payment.created", {"id": str(event_id)})


async def test_published_event_lands_in_the_new_queue(
    broker: RabbitBroker, rabbitmq_url: str
) -> None:
    event_id = uuid4()
    await publish_event(broker, event_id)

    message = await take(rabbitmq_url, topology.NEW_KEY)

    assert message is not None
    assert message.message_id == str(event_id)


async def test_message_carries_the_event_type_and_zero_attempt(
    broker: RabbitBroker, rabbitmq_url: str
) -> None:
    event_id = uuid4()
    await publish_event(broker, event_id)

    message = await take(rabbitmq_url, topology.NEW_KEY)

    assert message is not None
    assert message.headers["x-event-type"] == "payment.created"
    assert message.headers["x-attempt"] == 0


async def test_published_event_survives_a_broker_restart(
    broker: RabbitBroker, rabbitmq_url: str
) -> None:
    event_id = uuid4()
    await publish_event(broker, event_id)

    message = await take(rabbitmq_url, topology.NEW_KEY)

    assert message is not None
    assert message.delivery_mode == aio_pika.abc.DeliveryMode.PERSISTENT


async def test_unroutable_publish_raises_instead_of_vanishing(broker: RabbitBroker) -> None:
    with pytest.raises(aio_pika.exceptions.DeliveryError):
        await broker.publish(
            {"id": "orphan"},
            routing_key="payments.nowhere",
            exchange=topology.payments_exchange,
            mandatory=True,
        )
