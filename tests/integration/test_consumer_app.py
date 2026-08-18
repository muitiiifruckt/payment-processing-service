import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from faststream import TestApp
from faststream.rabbit import RabbitBroker
from faststream.rabbit.testing import TestRabbitBroker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.create_payment import PAYMENT_CREATED
from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.domain.status import PaymentStatus
from app.infrastructure.db.outbox_repository import OutboxRepository
from app.infrastructure.db.payment_repository import PaymentRepository
from app.infrastructure.gateway import EmulatedGateway
from app.presentation.consumer import build_app

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class SilentSender:
    async def send(self, url: str, payload: dict[str, object]) -> None: ...


class TickingClock:
    def __init__(self) -> None:
        self._now = NOW

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


@pytest.fixture
async def queued(session_factory: async_sessionmaker[AsyncSession]) -> Payment:
    """Платёж и событие о нём в outbox — ровно то, что оставляет API."""
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
        await OutboxRepository(session).add(
            event_id=uuid4(),
            aggregate_id=payment_id,
            event_type=PAYMENT_CREATED,
            payload={"event_id": str(uuid4()), "payment_id": str(payment_id)},
            now=NOW,
        )
    return payment


async def status_of(
    sessions: async_sessionmaker[AsyncSession], payment_id: UUID
) -> PaymentStatus | None:
    async with sessions() as session:
        found = await PaymentRepository(session).get(payment_id)
    return found.status if found else None


async def until(predicate: Any, *, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("условие так и не выполнилось")


async def test_consumer_process_drains_the_outbox_and_processes_the_payment(
    session_factory: async_sessionmaker[AsyncSession], queued: Payment
) -> None:
    broker = RabbitBroker()
    clock = TickingClock()
    app = build_app(
        broker,
        gateway=EmulatedGateway(clock, "succeeded"),
        clock=clock,
        sessions=session_factory,
        sender=SilentSender(),
    )

    async def succeeded() -> bool:
        return await status_of(session_factory, queued.payment_id) is PaymentStatus.SUCCEEDED

    async with TestRabbitBroker(broker), TestApp(app):
        await until(succeeded)


async def test_relay_task_does_not_outlive_the_application(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    broker = RabbitBroker()
    clock = TickingClock()
    app = build_app(
        broker,
        gateway=EmulatedGateway(clock, "succeeded"),
        clock=clock,
        sessions=session_factory,
        sender=SilentSender(),
    )

    async with TestRabbitBroker(broker), TestApp(app):
        pass

    assert not [task for task in asyncio.all_tasks() if "relay_forever" in repr(task)]
