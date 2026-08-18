from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from faststream.rabbit import RabbitBroker
from faststream.rabbit.testing import TestRabbitBroker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.create_payment import PAYMENT_CREATED
from app.application.process_payment import PermanentError
from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.domain.status import PaymentStatus
from app.infrastructure.broker import topology
from app.infrastructure.db.payment_repository import PaymentRepository
from app.presentation.consumer import register_handlers

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class FrozenClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class AlwaysSucceeds:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    async def process(self, payment_id: UUID) -> bool:
        self.calls.append(payment_id)
        return True


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


def an_event(payment_id: UUID) -> dict[str, str]:
    return {"event_id": str(uuid4()), "payment_id": str(payment_id)}


async def test_event_from_the_queue_processes_the_payment(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    broker = RabbitBroker()
    gateway = AlwaysSucceeds()
    register_handlers(broker, gateway=gateway, clock=FrozenClock(), sessions=session_factory)

    async with TestRabbitBroker(broker) as test:
        await test.publish(
            an_event(stored.payment_id),
            routing_key=topology.NEW_KEY,
            exchange=topology.payments_exchange,
            headers={"x-event-type": PAYMENT_CREATED, "x-attempt": 0},
        )

    assert gateway.calls == [stored.payment_id]

    async with session_factory() as session:
        after = await PaymentRepository(session).get(stored.payment_id)
    assert after is not None
    assert after.status is PaymentStatus.SUCCEEDED


async def test_redelivered_event_does_not_reach_the_gateway_twice(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    broker = RabbitBroker()
    gateway = AlwaysSucceeds()
    register_handlers(broker, gateway=gateway, clock=FrozenClock(), sessions=session_factory)
    event = an_event(stored.payment_id)

    async with TestRabbitBroker(broker) as test:
        for _ in range(2):
            await test.publish(
                event,
                routing_key=topology.NEW_KEY,
                exchange=topology.payments_exchange,
                headers={"x-event-type": PAYMENT_CREATED, "x-attempt": 0},
            )

    assert gateway.calls == [stored.payment_id]


async def test_event_of_another_type_is_not_processed(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    broker = RabbitBroker()
    gateway = AlwaysSucceeds()
    register_handlers(broker, gateway=gateway, clock=FrozenClock(), sessions=session_factory)

    async with TestRabbitBroker(broker) as test:
        with pytest.raises(PermanentError):
            await test.publish(
                an_event(stored.payment_id),
                routing_key=topology.NEW_KEY,
                exchange=topology.payments_exchange,
                headers={"x-event-type": "payment.refunded", "x-attempt": 0},
            )

    assert gateway.calls == []
