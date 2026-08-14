import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.create_payment import create_payment
from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.infrastructure.db.models import OutboxRow, PaymentRow

PAYMENT_ID = UUID("0192f3a4-0000-7000-8000-000000000020")
OTHER_ID = UUID("0192f3a4-0000-7000-8000-000000000021")
ATOMIC_ID = UUID("0192f3a4-0000-7000-8000-000000000022")
EVENT_ID = UUID("0192f3a4-0000-7000-8000-0000000000e0")
OTHER_EVENT_ID = UUID("0192f3a4-0000-7000-8000-0000000000e1")
RACE_ID_A = UUID("0192f3a4-0000-7000-8000-00000000002a")
RACE_ID_B = UUID("0192f3a4-0000-7000-8000-00000000002b")
RACE_EVENT_A = UUID("0192f3a4-0000-7000-8000-0000000000ea")
RACE_EVENT_B = UUID("0192f3a4-0000-7000-8000-0000000000eb")
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def a_payment(**overrides: object) -> Payment:
    defaults: dict[str, object] = {
        "payment_id": PAYMENT_ID,
        "amount": Money(Decimal("100.00"), Currency.RUB),
        "created_at": NOW,
        "idempotency_key": "key-create-1",
        "request_hash": "0" * 64,
    }
    return Payment(**(defaults | overrides))  # type: ignore[arg-type]


async def test_creating_a_payment_writes_exactly_one_outbox_event(
    session: AsyncSession,
) -> None:
    payment = a_payment()

    await create_payment(session, payment, event_id=EVENT_ID, now=NOW)
    await session.flush()

    events = await session.scalar(
        select(func.count()).select_from(OutboxRow).where(OutboxRow.aggregate_id == PAYMENT_ID)
    )
    assert events == 1


async def test_payment_and_event_are_written_atomically(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    payment = a_payment(payment_id=ATOMIC_ID, idempotency_key="key-atomic")

    with pytest.raises(RuntimeError):
        async with session_factory() as writer, writer.begin():
            await create_payment(writer, payment, event_id=OTHER_EVENT_ID, now=NOW)
            raise RuntimeError("сбой между записью и коммитом")

    async with session_factory() as reader:
        payments = await reader.scalar(
            select(func.count()).select_from(PaymentRow).where(PaymentRow.payment_id == ATOMIC_ID)
        )
        events = await reader.scalar(
            select(func.count()).select_from(OutboxRow).where(OutboxRow.aggregate_id == ATOMIC_ID)
        )

    assert (payments, events) == (0, 0)


async def test_second_payment_with_the_same_key_is_not_created(session: AsyncSession) -> None:
    first = await create_payment(session, a_payment(), event_id=EVENT_ID, now=NOW)

    second = await create_payment(
        session,
        a_payment(payment_id=OTHER_ID),
        event_id=OTHER_EVENT_ID,
        now=NOW,
    )
    await session.flush()

    payments = await session.scalar(
        select(func.count())
        .select_from(PaymentRow)
        .where(PaymentRow.idempotency_key == first.idempotency_key)
    )

    assert second.payment_id == first.payment_id
    assert payments == 1


async def test_concurrent_creates_with_the_same_key_yield_one_payment(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    key = "key-race"

    async def attempt(payment_id: UUID, event_id: UUID) -> Payment:
        async with session_factory() as writer, writer.begin():
            return await create_payment(
                writer,
                a_payment(payment_id=payment_id, idempotency_key=key),
                event_id=event_id,
                now=NOW,
            )

    results = await asyncio.gather(
        attempt(RACE_ID_A, RACE_EVENT_A),
        attempt(RACE_ID_B, RACE_EVENT_B),
        return_exceptions=True,
    )

    assert [r for r in results if isinstance(r, BaseException)] == []

    async with session_factory() as reader:
        payments = await reader.scalar(
            select(func.count()).select_from(PaymentRow).where(PaymentRow.idempotency_key == key)
        )
        winner = await reader.scalar(
            select(PaymentRow.payment_id).where(PaymentRow.idempotency_key == key)
        )
        events = await reader.scalar(
            select(func.count()).select_from(OutboxRow).where(OutboxRow.aggregate_id == winner)
        )

    assert (payments, events) == (1, 1)
    assert {r.payment_id for r in results if isinstance(r, Payment)} == {winner}
