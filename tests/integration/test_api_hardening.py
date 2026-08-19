from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.policy import CLAIM_LEASE
from app.infrastructure.config import settings
from app.infrastructure.db.models import OutboxRow
from app.infrastructure.db.payment_repository import PaymentRepository
from app.main import create_app
from app.presentation.api.deps import get_session

KEY = {"X-API-Key": settings.api_key}
NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def a_body(**overrides: Any) -> dict[str, Any]:
    return {"amount": "100.00", "currency": "RUB"} | overrides


def headers(key: str) -> dict[str, str]:
    return KEY | {"Idempotency-Key": key}


async def test_non_ascii_api_key_is_rejected_not_crashed(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/payments",
        json=a_body(),
        # байтами: httpx не пустит non-ascii строку, а curl такой заголовок отправит,
        # и Starlette раскодирует его в latin-1
        headers={"X-API-Key": "café".encode("latin-1"), "Idempotency-Key": b"h1"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_amount_above_the_domain_limit_is_a_validation_error(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/payments",
        json=a_body(amount="10000000000000000.00"),
        headers=headers("h2"),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_idempotent_replay_returns_the_full_representation(client: AsyncClient) -> None:
    """RFC §8.2: повтор отвечает полным представлением, а не тремя полями
    краткого."""
    first = await client.post("/api/v1/payments", json=a_body(), headers=headers("h3"))
    replay = await client.post("/api/v1/payments", json=a_body(), headers=headers("h3"))

    assert replay.status_code == first.status_code == 202
    assert replay.json()["payment_id"] == first.json()["payment_id"]
    assert set(replay.json()) == {
        "payment_id",
        "amount",
        "currency",
        "description",
        "metadata",
        "status",
        "webhook_url",
        "created_at",
        "processed_at",
    }


async def test_replay_after_processing_shows_the_terminal_status(
    client: AsyncClient, session: AsyncSession
) -> None:
    """Платёж мог обработаться между первым запросом и повтором. Статус
    отдавался фактический и раньше; красным здесь был processed_at."""
    first = await client.post("/api/v1/payments", json=a_body(), headers=headers("h4"))
    payment_id = UUID(first.json()["payment_id"])

    repository = PaymentRepository(session)
    stored = await repository.get(payment_id)
    assert stored is not None
    assert await repository.claim(payment_id, now=NOW, lease=CLAIM_LEASE)
    stored.mark_succeeded(NOW)
    assert await repository.save_result(stored, claimed_at=NOW)

    replay = await client.post("/api/v1/payments", json=a_body(), headers=headers("h4"))

    assert replay.json()["status"] == "succeeded"
    assert replay.json()["processed_at"] is not None


async def test_a_repeated_key_does_not_add_a_second_outbox_event(
    client: AsyncClient, session: AsyncSession
) -> None:
    await client.post("/api/v1/payments", json=a_body(), headers=headers("h5"))
    await client.post("/api/v1/payments", json=a_body(), headers=headers("h5"))

    events = await session.scalar(select(func.count()).select_from(OutboxRow))

    assert events == 1
