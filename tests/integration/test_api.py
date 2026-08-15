from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.config import settings
from app.main import create_app
from app.presentation.api.deps import get_session

KEY = {"X-API-Key": settings.api_key}


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


async def test_creating_a_payment_returns_accepted(client: AsyncClient) -> None:
    response = await client.post("/api/v1/payments", json=a_body(), headers=headers("k1"))

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert set(body) == {"payment_id", "status", "created_at"}


async def test_created_payment_is_readable(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/payments",
        json=a_body(amount="10.5", description="кофе", metadata={"a": 1}),
        headers=headers("k2"),
    )
    payment_id = created.json()["payment_id"]

    response = await client.get(f"/api/v1/payments/{payment_id}", headers=KEY)

    assert response.status_code == 200
    assert response.json() == {
        "payment_id": payment_id,
        "amount": "10.50",
        "currency": "RUB",
        "description": "кофе",
        "metadata": {"a": 1},
        "status": "pending",
        "webhook_url": None,
        "created_at": response.json()["created_at"],
        "processed_at": None,
    }


async def test_repeated_key_with_the_same_body_returns_the_same_payment(
    client: AsyncClient,
) -> None:
    first = await client.post("/api/v1/payments", json=a_body(), headers=headers("k3"))
    second = await client.post("/api/v1/payments", json=a_body(), headers=headers("k3"))

    assert second.status_code == 202
    assert second.json()["payment_id"] == first.json()["payment_id"]


async def test_key_order_in_metadata_does_not_conflict(client: AsyncClient) -> None:
    first = await client.post(
        "/api/v1/payments", json=a_body(metadata={"a": 1, "b": 2}), headers=headers("k4")
    )
    second = await client.post(
        "/api/v1/payments", json=a_body(metadata={"b": 2, "a": 1}), headers=headers("k4")
    )

    assert second.status_code == 202
    assert second.json()["payment_id"] == first.json()["payment_id"]


async def test_amount_written_differently_does_not_conflict(client: AsyncClient) -> None:
    await client.post("/api/v1/payments", json=a_body(amount="100"), headers=headers("k5"))
    second = await client.post(
        "/api/v1/payments", json=a_body(amount="100.00"), headers=headers("k5")
    )

    assert second.status_code == 202


async def test_repeated_key_with_a_different_body_conflicts(client: AsyncClient) -> None:
    await client.post("/api/v1/payments", json=a_body(), headers=headers("k6"))
    second = await client.post(
        "/api/v1/payments", json=a_body(amount="200.00"), headers=headers("k6")
    )

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_key_conflict"


async def test_payment_without_optional_fields_is_created(client: AsyncClient) -> None:
    response = await client.post("/api/v1/payments", json=a_body(), headers=headers("k7"))

    assert response.status_code == 202


@pytest.mark.parametrize(
    "body",
    [
        {"amount": "100.00", "currency": "GBP"},
        {"amount": "0", "currency": "RUB"},
        {"amount": "-1.00", "currency": "RUB"},
        {"amount": "1.001", "currency": "RUB"},
        {"amount": "100.00", "currency": "RUB", "webhook_url": "ftp://example.com"},
        {"amount": "100.00", "currency": "RUB", "description": "x" * 513},
        {"amount": "100.00", "currency": "RUB", "metadata": {"big": "x" * 9000}},
        {"amount": "100.00", "currency": "RUB", "metadata": [1, 2]},
    ],
)
async def test_invalid_body_is_rejected(client: AsyncClient, body: dict[str, Any]) -> None:
    response = await client.post("/api/v1/payments", json=body, headers=headers("k-invalid"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_missing_idempotency_key_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/api/v1/payments", json=a_body(), headers=KEY)

    assert response.status_code == 422


async def test_missing_api_key_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/payments", json=a_body(), headers={"Idempotency-Key": "k8"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_wrong_api_key_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/payments",
        json=a_body(),
        headers={"X-API-Key": "nope", "Idempotency-Key": "k9"},
    )

    assert response.status_code == 401


async def test_reading_without_api_key_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/api/v1/payments/0192f3a4-0000-7000-8000-0000000000ff")

    assert response.status_code == 401


async def test_unknown_payment_is_not_found(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/payments/0192f3a4-0000-7000-8000-0000000000ff", headers=KEY
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_malformed_payment_id_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/api/v1/payments/not-a-uuid", headers=KEY)

    assert response.status_code == 422


async def test_health_needs_no_api_key(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
