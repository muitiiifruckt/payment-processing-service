from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.infrastructure.db.payment_repository import PaymentRepository

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
LEASE = timedelta(seconds=10)


async def stored_payment(session: AsyncSession, payment_id: UUID, key: str) -> Payment:
    payment = Payment(
        payment_id=payment_id,
        amount=Money(Decimal("100.00"), Currency.RUB),
        created_at=NOW,
        idempotency_key=key,
        request_hash="0" * 64,
    )
    await PaymentRepository(session).add_if_absent(payment)
    return payment


async def test_claiming_a_pending_payment_succeeds(session: AsyncSession) -> None:
    payment_id = UUID("0192f3a4-0000-7000-8000-000000000030")
    await stored_payment(session, payment_id, "key-claim-1")
    repository = PaymentRepository(session)

    assert await repository.claim(payment_id, now=NOW, lease=LEASE)


async def test_second_claim_within_the_lease_is_refused(session: AsyncSession) -> None:
    payment_id = UUID("0192f3a4-0000-7000-8000-000000000031")
    await stored_payment(session, payment_id, "key-claim-2")
    repository = PaymentRepository(session)

    await repository.claim(payment_id, now=NOW, lease=LEASE)

    assert not await repository.claim(payment_id, now=NOW + LEASE, lease=LEASE)


async def test_claim_succeeds_again_once_the_lease_expired(session: AsyncSession) -> None:
    payment_id = UUID("0192f3a4-0000-7000-8000-000000000032")
    await stored_payment(session, payment_id, "key-claim-3")
    repository = PaymentRepository(session)

    await repository.claim(payment_id, now=NOW, lease=LEASE)

    assert await repository.claim(payment_id, now=NOW + LEASE + timedelta(seconds=1), lease=LEASE)


async def test_released_claim_is_available_immediately(session: AsyncSession) -> None:
    payment_id = UUID("0192f3a4-0000-7000-8000-000000000033")
    await stored_payment(session, payment_id, "key-claim-4")
    repository = PaymentRepository(session)

    await repository.claim(payment_id, now=NOW, lease=LEASE)
    await repository.release(payment_id)

    assert await repository.claim(payment_id, now=NOW, lease=LEASE)


async def test_terminal_payment_cannot_be_claimed(session: AsyncSession) -> None:
    payment_id = UUID("0192f3a4-0000-7000-8000-000000000034")
    payment = await stored_payment(session, payment_id, "key-claim-5")
    repository = PaymentRepository(session)

    payment.mark_succeeded(now=NOW)
    await repository.save_result(payment)

    assert not await repository.claim(payment_id, now=NOW, lease=LEASE)
