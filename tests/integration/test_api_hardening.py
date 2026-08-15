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


async def test_idempotent_replay_repeats_the_original_response(client: AsyncClient) -> None:
    first = await client.post("/api/v1/payments", json=a_body(), headers=headers("h3"))
    replay = await client.post("/api/v1/payments", json=a_body(), headers=headers("h3"))

    assert replay.status_code == first.status_code
    assert set(replay.json()) == set(first.json())
    assert replay.json()["payment_id"] == first.json()["payment_id"]


async def test_unexpected_failure_keeps_the_error_envelope(session: AsyncSession) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("пароль в тексте исключения")

    # без этого клиент перевозбуждает исключение вместо разбора ответа
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    # деталь исключения наружу не уходит
    assert "пароль" not in response.text


async def test_health_checks_the_database(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
