import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import aio_pika
import pytest
from faststream import TestApp
from faststream.rabbit import RabbitBroker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.policy import RETRY_QUEUE_DELAYS
from app.infrastructure.broker import topology
from app.infrastructure.broker.broker import RabbitEventPublisher, declare_topology, make_broker
from app.infrastructure.clock import SystemClock
from app.presentation.consumer import build_app


@pytest.fixture
async def broker(rabbitmq_url: str) -> AsyncIterator[RabbitBroker]:
    broker = make_broker(rabbitmq_url)
    await broker.connect()
    await declare_topology(broker)
    # очереди durable и общие на всю сессию: не вычистив их, тест видит
    # сообщение соседа, а сообщение из retry-очереди доезжает по TTL уже
    # после чужой очистки — порядок прогона начинает влиять на результат
    for queue, _ in topology.BINDINGS:
        await (await broker.declare_queue(queue)).purge()
    yield broker
    await broker.stop()


async def take(
    rabbitmq_url: str, queue_name: str, *, timeout: float = 5.0
) -> aio_pika.abc.AbstractIncomingMessage | None:
    """Одно сообщение отдельным соединением: подписка FastStream здесь лишняя.
    Поллинг, а не одна попытка: сообщение из retry-очереди приходит по TTL."""
    connection = await aio_pika.connect_robust(rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        queue = await channel.get_queue(queue_name, ensure=False)
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            message = await queue.get(fail=False, timeout=5)
            if message is not None:
                await message.ack()
                return message
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(0.2)


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


async def test_published_event_is_written_to_disk(broker: RabbitBroker, rabbitmq_url: str) -> None:
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


class _NeverCalled:
    """Ни шлюз, ни отправитель в этом тесте не нужны: сообщений нет."""

    async def process(self, payment_id: UUID) -> bool:  # pragma: no cover
        raise AssertionError("шлюз не должен вызываться")

    async def send(self, url: str, payload: dict[str, Any]) -> None:  # pragma: no cover
        raise AssertionError("отправитель не должен вызываться")


async def _delete_queues(rabbitmq_url: str) -> None:
    """Очереди durable и переживают предыдущие тесты — без удаления
    проверка «объявились на старте» проходит сама собой."""
    connection = await aio_pika.connect_robust(rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        for attempt in range(1, len(RETRY_QUEUE_DELAYS) + 1):
            await channel.queue_delete(topology.retry_key(attempt))
        await channel.queue_delete(topology.DLQ_KEY)


async def test_starting_the_consumer_declares_the_retry_queues_and_the_dlq(
    rabbitmq_url: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Очереди объявляются подъёмом приложения, а не первым попавшим
    сообщением: иначе первый же отказ на холодном старте уходит в никуда."""
    await _delete_queues(rabbitmq_url)
    app = build_app(
        make_broker(rabbitmq_url),
        gateway=_NeverCalled(),
        clock=SystemClock(),
        sessions=session_factory,
        sender=_NeverCalled(),
    )
    async with TestApp(app):
        pass

    connection = await aio_pika.connect_robust(rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        for attempt in range(1, len(RETRY_QUEUE_DELAYS) + 1):
            await channel.get_queue(topology.retry_key(attempt), ensure=True)
        await channel.get_queue(topology.DLQ_KEY, ensure=True)


async def test_a_message_returns_from_the_retry_queue_when_the_delay_expires(
    broker: RabbitBroker, rabbitmq_url: str
) -> None:
    event_id = uuid4()
    await broker.publish(
        {"event_id": str(event_id)},
        routing_key=topology.retry_key(1),
        exchange=topology.payments_exchange,
        headers={"x-event-type": "payment.created", "x-attempt": 1},
        persist=True,
        mandatory=True,
    )

    # TTL первой retry-очереди — 2 секунды, ждём с запасом и поллингом
    message = await take(rabbitmq_url, topology.NEW_KEY, timeout=10)

    assert message is not None
    assert message.headers["x-attempt"] == 1
    assert json.loads(message.body)["event_id"] == str(event_id)
