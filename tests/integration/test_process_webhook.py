from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.notify import WebhookUnavailableError
from app.application.process_payment import process_payment
from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.domain.status import PaymentStatus
from app.infrastructure.db.payment_repository import PaymentRepository

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
HOOK = "https://receiver.test/hook"


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


class RecordingSender:
    def __init__(self, *outcomes: Exception | None) -> None:
        self._outcomes = list(outcomes) or [None]
        self.calls: list[dict[str, Any]] = []

    async def send(self, url: str, payload: dict[str, Any]) -> None:
        self.calls.append(payload)
        outcome = self._outcomes[min(len(self.calls), len(self._outcomes)) - 1]
        if outcome is not None:
            raise outcome


@pytest.fixture
async def with_hook(session_factory: async_sessionmaker[AsyncSession]) -> Payment:
    payment_id = uuid4()
    payment = Payment(
        payment_id=payment_id,
        amount=Money(Decimal("100.00"), Currency.RUB),
        created_at=NOW,
        idempotency_key=str(payment_id),
        request_hash="0" * 64,
        webhook_url=HOOK,
    )
    async with session_factory() as session, session.begin():
        await PaymentRepository(session).add_if_absent(payment)
    return payment


async def reload(sessions: async_sessionmaker[AsyncSession], payment_id: UUID) -> Payment:
    async with sessions() as session:
        found = await PaymentRepository(session).get(payment_id)
    assert found is not None
    return found


async def run(
    sessions: async_sessionmaker[AsyncSession], payment_id: UUID, sender: RecordingSender
) -> None:
    await process_payment(
        sessions,
        payment_id,
        gateway=AlwaysSucceeds(),
        clock=FrozenClock(),
        sender=sender,
        event_id=uuid4(),
    )


async def test_successful_processing_notifies_the_receiver(
    session_factory: async_sessionmaker[AsyncSession], with_hook: Payment
) -> None:
    sender = RecordingSender()

    await run(session_factory, with_hook.payment_id, sender)

    assert len(sender.calls) == 1
    assert sender.calls[0]["payment_id"] == str(with_hook.payment_id)
    after = await reload(session_factory, with_hook.payment_id)
    assert after.webhook_delivered_at is not None


async def test_redelivery_after_a_delivered_webhook_sends_nothing(
    session_factory: async_sessionmaker[AsyncSession], with_hook: Payment
) -> None:
    await run(session_factory, with_hook.payment_id, RecordingSender())

    second = RecordingSender()
    await run(session_factory, with_hook.payment_id, second)

    assert second.calls == []


async def test_redelivery_after_a_failed_webhook_retries_only_the_webhook(
    session_factory: async_sessionmaker[AsyncSession], with_hook: Payment
) -> None:
    failing = RecordingSender(WebhookUnavailableError("503"))
    await run(session_factory, with_hook.payment_id, failing)
    assert len(failing.calls) == 3  # первый прогон: три попытки

    after = await reload(session_factory, with_hook.payment_id)
    assert after.status is PaymentStatus.SUCCEEDED
    assert after.webhook_delivered_at is None

    second = RecordingSender()
    await run(session_factory, with_hook.payment_id, second)

    assert len(second.calls) == 1  # повторный прогон: одна попытка
    assert (await reload(session_factory, with_hook.payment_id)).webhook_delivered_at is not None


async def test_redelivery_for_a_payment_without_a_hook_does_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
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

    await run(session_factory, payment_id, RecordingSender())
    second = RecordingSender()
    await run(session_factory, payment_id, second)

    assert second.calls == []
    assert (await reload(session_factory, payment_id)).status is PaymentStatus.SUCCEEDED


async def test_database_is_not_held_while_the_receiver_is_called(
    session_factory: async_sessionmaker[AsyncSession], with_hook: Payment
) -> None:
    seen: list[PaymentStatus] = []

    class ObservingSender:
        async def send(self, url: str, payload: dict[str, Any]) -> None:
            # исход уже зафиксирован и виден снаружи — значит транзакция
            # закрыта до обращения к получателю, а не держится на всё время
            async with session_factory() as session:
                other = await PaymentRepository(session).get(with_hook.payment_id)
            assert other is not None
            seen.append(other.status)

    await process_payment(
        session_factory,
        with_hook.payment_id,
        gateway=AlwaysSucceeds(),
        clock=FrozenClock(),
        sender=ObservingSender(),
        event_id=uuid4(),
    )

    assert seen == [PaymentStatus.SUCCEEDED]
