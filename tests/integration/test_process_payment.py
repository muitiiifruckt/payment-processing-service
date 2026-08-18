from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.policy import CLAIM_LEASE
from app.application.ports import GatewayUnavailableError
from app.application.process_payment import (
    PermanentError,
    TransientError,
    process_payment,
)
from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.domain.status import PaymentStatus
from app.infrastructure.db.payment_repository import PaymentRepository

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


class AlwaysFails(AlwaysSucceeds):
    async def process(self, payment_id: UUID) -> bool:
        self.calls.append(payment_id)
        return False


class AlwaysUnavailable(AlwaysSucceeds):
    async def process(self, payment_id: UUID) -> bool:
        self.calls.append(payment_id)
        raise GatewayUnavailableError("шлюз не отвечает")


def a_payment(payment_id: UUID, **overrides: object) -> Payment:
    defaults: dict[str, object] = {
        "payment_id": payment_id,
        "amount": Money(Decimal("100.00"), Currency.RUB),
        "created_at": NOW,
        "idempotency_key": str(payment_id),
        "request_hash": "0" * 64,
    }
    return Payment(**(defaults | overrides))  # type: ignore[arg-type]


@pytest.fixture
async def stored(session_factory: async_sessionmaker[AsyncSession]) -> Payment:
    payment = a_payment(uuid4())
    async with session_factory() as session, session.begin():
        await PaymentRepository(session).add_if_absent(payment)
    return payment


async def reload(sessions: async_sessionmaker[AsyncSession], payment_id: UUID) -> Payment:
    async with sessions() as session:
        found = await PaymentRepository(session).get(payment_id)
    assert found is not None
    return found


async def test_successful_processing_moves_the_payment_to_succeeded(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    clock = FrozenClock()

    await process_payment(session_factory, stored.payment_id, gateway=AlwaysSucceeds(), clock=clock)

    after = await reload(session_factory, stored.payment_id)
    assert after.status is PaymentStatus.SUCCEEDED
    assert after.processed_at == clock.now()


async def test_declined_payment_moves_to_failed(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    await process_payment(
        session_factory, stored.payment_id, gateway=AlwaysFails(), clock=FrozenClock()
    )

    after = await reload(session_factory, stored.payment_id)
    assert after.status is PaymentStatus.FAILED
    assert after.processed_at is not None


async def test_redelivery_does_not_call_the_gateway_again(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    gateway = AlwaysSucceeds()

    await process_payment(session_factory, stored.payment_id, gateway=gateway, clock=FrozenClock())
    await process_payment(session_factory, stored.payment_id, gateway=gateway, clock=FrozenClock())

    assert gateway.calls == [stored.payment_id]


async def test_redelivery_keeps_the_terminal_status(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    await process_payment(
        session_factory, stored.payment_id, gateway=AlwaysFails(), clock=FrozenClock()
    )

    await process_payment(
        session_factory, stored.payment_id, gateway=AlwaysSucceeds(), clock=FrozenClock()
    )

    after = await reload(session_factory, stored.payment_id)
    assert after.status is PaymentStatus.FAILED


async def test_unavailable_gateway_leaves_the_payment_pending_and_asks_for_a_retry(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    with pytest.raises(TransientError):
        await process_payment(
            session_factory,
            stored.payment_id,
            gateway=AlwaysUnavailable(),
            clock=FrozenClock(),
        )

    after = await reload(session_factory, stored.payment_id)
    assert after.status is PaymentStatus.PENDING
    assert after.processed_at is None


async def test_unavailable_gateway_releases_the_claim(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    with pytest.raises(TransientError):
        await process_payment(
            session_factory,
            stored.payment_id,
            gateway=AlwaysUnavailable(),
            clock=FrozenClock(),
        )

    # захват снят живым обработчиком, ждать протухания незачем
    async with session_factory() as session, session.begin():
        assert await PaymentRepository(session).claim(stored.payment_id, now=NOW, lease=CLAIM_LEASE)


async def test_payment_held_by_another_worker_goes_to_a_retry(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    async with session_factory() as session, session.begin():
        await PaymentRepository(session).claim(stored.payment_id, now=NOW, lease=CLAIM_LEASE)

    gateway = AlwaysSucceeds()
    with pytest.raises(TransientError):
        await process_payment(
            session_factory, stored.payment_id, gateway=gateway, clock=FrozenClock()
        )

    assert gateway.calls == []


async def test_event_about_an_unknown_payment_is_permanent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(PermanentError):
        await process_payment(
            session_factory, uuid4(), gateway=AlwaysSucceeds(), clock=FrozenClock()
        )


async def test_database_is_not_held_while_the_gateway_is_called(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    seen: list[datetime | None] = []

    class ObservingGateway:
        async def process(self, payment_id: UUID) -> bool:
            # захват уже зафиксирован и виден снаружи — значит транзакция
            # закрыта до обращения к шлюзу, а не удерживается на всё время
            async with session_factory() as session:
                other = await PaymentRepository(session).get(payment_id)
            seen.append(other.processed_at if other else None)
            assert other is not None
            return True

    await process_payment(
        session_factory, stored.payment_id, gateway=ObservingGateway(), clock=FrozenClock()
    )

    assert seen == [None]


async def test_declined_payment_is_treated_as_handled_and_not_retried(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    """Бизнес-отказ — это результат, а не сбой: исключения нет, значит
    сообщение подтверждается и в DLQ не уезжает."""
    await process_payment(
        session_factory, stored.payment_id, gateway=AlwaysFails(), clock=FrozenClock()
    )

    after = await reload(session_factory, stored.payment_id)
    assert after.status is PaymentStatus.FAILED


async def test_a_failing_release_does_not_hide_the_gateway_error(
    session_factory: async_sessionmaker[AsyncSession], stored: Payment
) -> None:
    """БД и шлюз отваливаются вместе. Если снятие захвата тоже не удалось,
    наружу обязан выйти TransientError, а не сырая ошибка драйвера: иначе
    сообщение уедет в DLQ с непонятной причиной."""

    class BreaksAfterTheClaim:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> AsyncSession:
            self.calls += 1
            if self.calls > 1:
                raise ConnectionError("соединение с БД потеряно")
            return session_factory()

    with pytest.raises(TransientError):
        await process_payment(
            BreaksAfterTheClaim(),  # type: ignore[arg-type]
            stored.payment_id,
            gateway=AlwaysUnavailable(),
            clock=FrozenClock(),
        )
