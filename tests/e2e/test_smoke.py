import asyncio
import os
import uuid
from typing import Any

import httpx
import pytest

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "local-dev-key")
SINK_URL = f"{BASE_URL}/__sink__/webhook"
TERMINAL = {"succeeded", "failed"}


@pytest.fixture
async def client() -> Any:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as client:
        yield client


async def create(client: httpx.AsyncClient, **body: Any) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/payments",
        json={"amount": "100.50", "currency": "RUB", **body},
        headers={"X-API-Key": API_KEY, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 202, response.text
    return response.json()


async def until(predicate: Any, *, timeout: float = 90.0) -> Any:
    """Поллинг с таймаутом, а не сон на глазок: обработка длится 2–5 секунд
    плюс задержки повторов."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        found = await predicate()
        if found is not None:
            return found
        await asyncio.sleep(1)
    raise AssertionError("условие так и не выполнилось за отведённое время")


async def test_a_payment_reaches_a_terminal_status_and_the_webhook_arrives(
    client: httpx.AsyncClient,
) -> None:
    created = await create(client, webhook_url=SINK_URL)
    payment_id = created["payment_id"]

    async def settled() -> dict[str, Any] | None:
        response = await client.get(
            f"/api/v1/payments/{payment_id}", headers={"X-API-Key": API_KEY}
        )
        payment = response.json()
        return payment if payment["status"] in TERMINAL else None

    payment = await until(settled)
    assert payment["processed_at"] is not None

    async def delivered() -> dict[str, Any] | None:
        received = (await client.get("/__sink__/webhook")).json()
        matching = [event for event in received if event["payment_id"] == payment_id]
        return matching[0] if matching else None

    event = await until(delivered)
    assert event["status"] == payment["status"]
    assert event["amount"] == "100.50"
    assert event["currency"] == "RUB"


async def test_a_payment_without_a_webhook_url_still_reaches_a_terminal_status(
    client: httpx.AsyncClient,
) -> None:
    created = await create(client)

    async def settled() -> dict[str, Any] | None:
        response = await client.get(
            f"/api/v1/payments/{created['payment_id']}", headers={"X-API-Key": API_KEY}
        )
        payment = response.json()
        return payment if payment["status"] in TERMINAL else None

    assert (await until(settled))["status"] in TERMINAL


async def test_the_schema_was_created_automatically(client: httpx.AsyncClient) -> None:
    """Окружение поднялось с нуля: если бы миграции не накатились,
    /health не отдал бы ok по базе."""
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["database"] == "ok"
