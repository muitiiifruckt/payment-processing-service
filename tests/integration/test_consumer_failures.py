from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from faststream.rabbit import RabbitBroker, RabbitMessage
from faststream.rabbit.testing import TestRabbitBroker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.create_payment import PAYMENT_CREATED
from app.application.policy import MAX_BUSY_WAITS, MAX_HANDLER_RUNS
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


class AlwaysSucceeds:
    async def process(self, payment_id: UUID) -> bool:
        return True


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


@pytest.fixture
async def with_hook(session_factory: async_sessionmaker[AsyncSession]) -> Payment:
    payment_id = uuid4()
    payment = Payment(
        payment_id=payment_id,
        amount=Money(Decimal("100.00"), Currency.RUB),
        created_at=NOW,
        idempotency_key=str(payment_id),
        request_hash="0" * 64,
        webhook_url="https://receiver.test/hook",
    )
    async with session_factory() as session, session.begin():
        await PaymentRepository(session).add_if_absent(payment)
    return payment


def make_broker_with_spies(
    sessions: async_sessionmaker[AsyncSession], gateway: object, sender: object | None = None
) -> tuple[RabbitBroker, dict[str, list[tuple[bytes, dict[str, Any]]]]]:
    """Подписчики на retry-очереди и DLQ: иначе не видно, куда ушёл отказ."""
    broker = RabbitBroker()
    register_handlers(
        broker,
        gateway=gateway,  # type: ignore[arg-type]
        clock=FrozenClock(),
        sessions=sessions,
        sender=sender or SilentSender(),  # type: ignore[arg-type]
    )
    seen: dict[str, list[tuple[bytes, dict[str, Any]]]] = {}

    def spy_on(queue: Any, exchange: Any, name: str) -> None:
        seen[name] = []

        @broker.subscriber(queue, exchange)
        async def spy(message: RabbitMessage) -> None:
            # сырое тело: в DLQ приезжает и то, что не разобралось.
            # Заголовки — по ним видно счёт прогонов и класс отказа
            seen[name].append((bytes(message.body), dict(message.headers)))

    for attempt in range(1, MAX_HANDLER_RUNS):
        spy_on(topology.retry_queue(attempt), topology.payments_exchange, f"retry-{attempt}")
    spy_on(topology.dead_letter_queue, topology.dlx_exchange, "dlq")
    return broker, seen


async def deliver(
    broker: RabbitBroker,
    payment_id: UUID,
    *,
    attempt: int = 0,
    busy: int = 0,
    event_type: str = PAYMENT_CREATED,
) -> None:
    async with TestRabbitBroker(broker) as test:
        await test.publish(
            {"event_id": str(uuid4()), "payment_id": str(payment_id)},
            routing_key=topology.NEW_KEY,
            exchange=topology.payments_exchange,
            headers={"x-event-type": event_type, "x-attempt": attempt, "x-busy": busy},
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


class BrokenSender:
    """Ошибка, которой нет ни в одном списке классификации, и которая
    поднимается вне зоны ответственности process_payment."""

    async def send(self, url: str, payload: dict[str, Any]) -> None:
        raise ValueError("что-то пошло не так")


async def test_an_unclassified_exception_is_treated_as_temporary(
    session_factory: async_sessionmaker[AsyncSession], with_hook: Payment
) -> None:
    """Умолчание в пользу повтора: неучтённая ошибка не должна стоить
    сообщения (RFC §6.3). Исключение поднимается за пределами классификатора
    process_payment — иначе проверялся бы не тот механизм."""
    broker, seen = make_broker_with_spies(session_factory, AlwaysSucceeds(), sender=BrokenSender())

    await deliver(broker, with_hook.payment_id)

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
    session_factory: async_sessionmaker[AsyncSession],
    stored: Payment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если переслать не удалось, исключение обязано выйти из обработчика:
    иначе сообщение подтверждается, и платёж теряется без следа."""
    broker, _ = make_broker_with_spies(session_factory, Unavailable())

    async def refuse(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("брокер не принял публикацию")

    # подменяется только пересылка отказа, но не доставка исходного сообщения
    monkeypatch.setattr("app.presentation.consumer.route_failure", refuse)

    with pytest.raises(ConnectionError):
        await deliver(broker, stored.payment_id)


async def test_a_busy_payment_does_not_burn_the_retry_budget(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    """Обработчик умер с захватом, сообщение вернулось с уже потраченными
    прогонами. Занятость — не отказ обработки: списывать за неё прогоны
    значит уводить живой платёж в DLQ и оставлять его pending навсегда."""
    async with session_factory() as session, session.begin():
        await PaymentRepository(session).claim(
            stored.payment_id, now=NOW, lease=timedelta(seconds=10)
        )

    broker, seen = make_broker_with_spies(session_factory, AlwaysSucceeds())

    await deliver(broker, stored.payment_id, attempt=MAX_HANDLER_RUNS - 1)

    assert seen["dlq"] == []
    assert [headers["x-attempt"] for _, headers in seen["retry-1"]] == [MAX_HANDLER_RUNS - 1]


async def test_a_payment_busy_for_too_long_stops_waiting_and_spends_a_run(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    """Бесконечно ждать освобождения нельзя: у ожидания свой предел. Дальше
    занятость перестаёт быть бесплатной и считается обычным отказом —
    платёж получает оставшиеся прогоны и в конце концов уедет в DLQ."""
    async with session_factory() as session, session.begin():
        await PaymentRepository(session).claim(
            stored.payment_id, now=NOW, lease=timedelta(seconds=10)
        )

    broker, seen = make_broker_with_spies(session_factory, AlwaysSucceeds())

    await deliver(broker, stored.payment_id, attempt=0, busy=MAX_BUSY_WAITS)

    assert seen["dlq"] == []
    assert [headers["x-attempt"] for _, headers in seen["retry-1"]] == [1]
