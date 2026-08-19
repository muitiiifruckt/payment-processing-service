from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from faststream.rabbit import RabbitBroker
from faststream.rabbit.testing import TestRabbitBroker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.create_payment import PAYMENT_CREATED
from app.application.policy import MAX_HANDLER_RUNS
from app.application.ports import GatewayUnavailableError
from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.domain.status import PaymentStatus
from app.infrastructure.broker import topology
from app.infrastructure.db.payment_repository import PaymentRepository
from app.presentation.consumer import register_handlers

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class FrozenClock:
    def __init__(self) -> None:
        self._now = NOW

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class Unavailable:
    async def process(self, payment_id: UUID) -> bool:
        raise GatewayUnavailableError("шлюз не отвечает")


class SilentSender:
    async def send(self, url: str, payload: dict[str, Any]) -> None: ...


@pytest.fixture
async def stored(session_factory: async_sessionmaker[AsyncSession]) -> Payment:
    payment_id = uuid4()
    payment = Payment(
        payment_id=payment_id,
        amount=Money(Decimal("100.00"), Currency.RUB),
        created_at=NOW,
        idempotency_key=str(payment_id),
        request_hash="0" * 64,
    )
    async with session_factory() as session, session.begin():
        await PaymentRepository(session).add_if_absent(payment)
    return payment


def make_broker_with_spies(
    sessions: async_sessionmaker[AsyncSession], gateway: object
) -> tuple[RabbitBroker, dict[str, list[dict[str, Any]]]]:
    """Подписчики на retry-очереди и DLQ: иначе не видно, куда ушёл отказ."""
    broker = RabbitBroker()
    register_handlers(
        broker,
        gateway=gateway,  # type: ignore[arg-type]
        clock=FrozenClock(),
        sessions=sessions,
        sender=SilentSender(),
    )
    seen: dict[str, list[dict[str, Any]]] = {}

    def spy_on(queue: Any, exchange: Any, name: str) -> None:
        seen[name] = []

        @broker.subscriber(queue, exchange)
        async def spy(event: dict[str, Any]) -> None:
            seen[name].append(event)

    for attempt in range(1, MAX_HANDLER_RUNS):
        spy_on(topology.retry_queue(attempt), topology.payments_exchange, f"retry-{attempt}")
    spy_on(topology.dead_letter_queue, topology.dlx_exchange, "dlq")
    return broker, seen


async def deliver(
    broker: RabbitBroker, payment_id: UUID, *, attempt: int = 0, event_type: str = PAYMENT_CREATED
) -> None:
    async with TestRabbitBroker(broker) as test:
        await test.publish(
            {"event_id": str(uuid4()), "payment_id": str(payment_id)},
            routing_key=topology.NEW_KEY,
            exchange=topology.payments_exchange,
            headers={"x-event-type": event_type, "x-attempt": attempt},
        )


async def test_a_temporary_failure_goes_to_the_first_retry_queue(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    broker, seen = make_broker_with_spies(session_factory, Unavailable())

    await deliver(broker, stored.payment_id)

    assert len(seen["retry-1"]) == 1
    assert seen["dlq"] == []


async def test_the_fourth_failed_run_goes_to_the_dead_letter_queue(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    broker, seen = make_broker_with_spies(session_factory, Unavailable())

    await deliver(broker, stored.payment_id, attempt=MAX_HANDLER_RUNS - 1)

    assert len(seen["dlq"]) == 1
    assert seen["retry-1"] == []


async def test_an_unknown_event_type_goes_straight_to_the_dead_letter_queue(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    broker, seen = make_broker_with_spies(session_factory, Unavailable())

    await deliver(broker, stored.payment_id, event_type="payment.refunded")

    assert len(seen["dlq"]) == 1
    assert seen["retry-1"] == []


async def test_an_event_about_an_unknown_payment_goes_straight_to_the_dead_letter_queue(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    broker, seen = make_broker_with_spies(session_factory, Unavailable())

    await deliver(broker, uuid4())

    assert len(seen["dlq"]) == 1
    assert seen["retry-1"] == []


async def test_a_repeatedly_failing_payment_does_not_stay_locked(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    broker, _ = make_broker_with_spies(session_factory, Unavailable())

    for attempt in range(MAX_HANDLER_RUNS):
        await deliver(broker, stored.payment_id, attempt=attempt)

    async with session_factory() as session:
        after = await PaymentRepository(session).get(stored.payment_id)
    assert after is not None
    assert after.status is PaymentStatus.PENDING
    # захват снят: платёж можно взять в работу немедленно, не дожидаясь протухания
    async with session_factory() as session, session.begin():
        assert await PaymentRepository(session).claim(
            stored.payment_id, now=NOW, lease=timedelta(seconds=10)
        )


class Broken:
    async def process(self, payment_id: UUID) -> bool:
        # ошибка, которой нет ни в одном списке классификации
        raise ValueError("что-то пошло не так")


async def test_an_unclassified_exception_is_treated_as_temporary(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    """Умолчание в пользу повтора: неучтённая ошибка не должна стоить
    сообщения (RFC §6.3)."""
    broker, seen = make_broker_with_spies(session_factory, Broken())

    await deliver(broker, stored.payment_id)

    assert len(seen["retry-1"]) == 1
    assert seen["dlq"] == []


async def test_an_unparseable_body_goes_straight_to_the_dead_letter_queue(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Разбор тела происходит до обработчика, и такое сообщение обошло бы
    механизм классификации целиком."""
    broker, seen = make_broker_with_spies(session_factory, Unavailable())

    async with TestRabbitBroker(broker) as test:
        await test.publish(
            bytes([0xFF]) + b" not json at all",
            routing_key=topology.NEW_KEY,
            exchange=topology.payments_exchange,
            headers={"x-event-type": PAYMENT_CREATED, "x-attempt": 0},
        )

    assert len(seen["dlq"]) == 1
    assert seen["retry-1"] == []


async def test_a_failed_retry_publish_does_not_acknowledge_the_message(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    """Если переслать не удалось, исходное сообщение обязано остаться
    неподтверждённым — иначе платёж теряется без следа."""
    broker, _ = make_broker_with_spies(session_factory, Unavailable())

    async def refuse(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("брокер не принял публикацию")

    with pytest.raises(ConnectionError):
        async with TestRabbitBroker(broker) as test:
            test.publish_ = refuse  # type: ignore[method-assign]
            broker.publish = refuse  # type: ignore[method-assign]
            await test.publish(
                {"event_id": str(uuid4()), "payment_id": str(stored.payment_id)},
                routing_key=topology.NEW_KEY,
                exchange=topology.payments_exchange,
                headers={"x-event-type": PAYMENT_CREATED, "x-attempt": 0},
            )
