from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.outbox_repository import OutboxRepository

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


async def add_event(session: AsyncSession, event_id: UUID, *, now: datetime = NOW) -> None:
    await OutboxRepository(session).add(
        event_id=event_id,
        aggregate_id=uuid4(),
        event_type="payment.created",
        payload={"event_id": str(event_id)},
        now=now,
    )


async def test_batch_returns_unpublished_events(session: AsyncSession) -> None:
    event_id = uuid4()
    await add_event(session, event_id)
    await session.flush()

    taken = await OutboxRepository(session).claim_batch(now=NOW, limit=10)

    assert event_id in {event.event_id for event in taken}


async def test_batch_skips_events_not_due_yet(session: AsyncSession) -> None:
    event_id = uuid4()
    await add_event(session, event_id, now=NOW + timedelta(minutes=5))
    await session.flush()

    taken = await OutboxRepository(session).claim_batch(now=NOW, limit=10)

    assert event_id not in {event.event_id for event in taken}


async def test_batch_skips_rows_held_by_another_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_id = uuid4()
    async with session_factory() as writer, writer.begin():
        await add_event(writer, event_id)

    try:
        async with session_factory() as first, first.begin():
            taken = await OutboxRepository(first).claim_batch(now=NOW, limit=10)
            assert event_id in {event.event_id for event in taken}

            async with session_factory() as second, second.begin():
                also_taken = await OutboxRepository(second).claim_batch(now=NOW, limit=10)

            assert event_id not in {event.event_id for event in also_taken}
    finally:
        async with session_factory() as cleanup, cleanup.begin():
            await OutboxRepository(cleanup).mark_published(event_id, now=NOW)
