from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.create_payment import create_payment
from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.infrastructure.db.models import OutboxRow

PAYMENT_ID = UUID("0192f3a4-0000-7000-8000-000000000020")
EVENT_ID = UUID("0192f3a4-0000-7000-8000-0000000000e0")
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
