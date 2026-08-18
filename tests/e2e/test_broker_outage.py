import asyncio
import os
import subprocess
import uuid
from typing import Any

import httpx
import pytest

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("E2E_API_KEY", "local-dev-key")
TERMINAL = {"succeeded", "failed"}


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], check=True, capture_output=True)


@pytest.fixture
async def stopped_broker() -> Any:
    compose("stop", "rabbitmq")
    try:
        yield
    finally:
        compose("start", "rabbitmq")


async def test_a_payment_created_while_the_broker_is_down_is_processed_after_it_returns(
    stopped_broker: None,
) -> None:
    """Событие лежит в outbox: relay опубликует его, как только брокер вернётся.
    Ради этого outbox и заведён."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        created = await client.post(
            "/api/v1/payments",
            json={"amount": "42.00", "currency": "RUB"},
            headers={"X-API-Key": API_KEY, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert created.status_code == 202, created.text
        payment_id = created.json()["payment_id"]

        compose("start", "rabbitmq")

        deadline = asyncio.get_running_loop().time() + 120
        while asyncio.get_running_loop().time() < deadline:
            payment = (
                await client.get(f"/api/v1/payments/{payment_id}", headers={"X-API-Key": API_KEY})
            ).json()
            if payment["status"] in TERMINAL:
                return
            await asyncio.sleep(2)

    raise AssertionError("платёж так и не обработался после возврата брокера")
