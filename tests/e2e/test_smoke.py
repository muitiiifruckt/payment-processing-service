import asyncio
import os
import subprocess
import uuid
from typing import Any

import httpx
import pytest

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8000")
# отдельная переменная: API_KEY в окружении прогона принадлежит тестовому
# приложению, а сквозной тест стучится в поднятое compose'ом
API_KEY = os.getenv("E2E_API_KEY", "local-dev-key")
# адрес, по которому приёмник видит consumer, а не мы с хоста
SINK_URL = os.getenv("E2E_SINK_URL", "http://api:8000/__sink__/webhook")
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
        received = (await client.get("/__sink__/webhook", headers={"X-API-Key": API_KEY})).json()
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


async def test_a_payment_with_an_unreachable_webhook_keeps_its_terminal_status(
    client: httpx.AsyncClient,
) -> None:
    """Недостижимый получатель уводит сообщение в DLQ, но исход платежа
    уже записан и остаётся неизменным."""
    created = await create(client, webhook_url="http://nowhere.invalid:9/hook")
    payment_id = created["payment_id"]

    async def settled() -> dict[str, Any] | None:
        payment = (
            await client.get(f"/api/v1/payments/{payment_id}", headers={"X-API-Key": API_KEY})
        ).json()
        return payment if payment["status"] in TERMINAL else None

    payment = await until(settled)
    status = payment["status"]

    # имя не существует, поэтому отказ постоянный: сообщение уходит в DLQ
    # сразу. Пауза — чтобы поймать чужую запись, если бы она случилась
    await asyncio.sleep(5)
    again = (
        await client.get(f"/api/v1/payments/{payment_id}", headers={"X-API-Key": API_KEY})
    ).json()

    assert again["status"] == status
    assert again["processed_at"] == payment["processed_at"]


async def test_a_receiver_that_refuses_twice_is_notified_after_the_retries(
    client: httpx.AsyncClient,
) -> None:
    """Две первые попытки получают 503, третья проходит: повторы webhook
    видны снаружи, а не только в логе."""
    flaky = SINK_URL.replace("/__sink__/webhook", "/__sink__/flaky/2")
    created = await create(client, webhook_url=flaky)
    payment_id = created["payment_id"]

    async def delivered() -> dict[str, Any] | None:
        received = (await client.get("/__sink__/webhook", headers={"X-API-Key": API_KEY})).json()
        matching = [event for event in received if event["payment_id"] == payment_id]
        return matching[0] if matching else None

    await until(delivered)

    counts = (await client.get("/__sink__/flaky", headers={"X-API-Key": API_KEY})).json()
    assert counts[payment_id] == 3


async def test_the_queues_and_messages_survive_a_broker_restart(
    client: httpx.AsyncClient,
) -> None:
    """Очереди durable, том у брокера есть: перезапуск не должен стирать
    ни топологию, ни ещё не разобранные сообщения."""
    subprocess.run(["docker", "compose", "restart", "rabbitmq"], check=True, capture_output=True)

    created = await create(client, webhook_url=SINK_URL)

    async def settled() -> dict[str, Any] | None:
        payment = (
            await client.get(
                f"/api/v1/payments/{created['payment_id']}", headers={"X-API-Key": API_KEY}
            )
        ).json()
        return payment if payment["status"] in TERMINAL else None

    assert (await until(settled))["status"] in TERMINAL


async def test_stopping_the_consumer_does_not_leave_a_payment_half_done(
    client: httpx.AsyncClient,
) -> None:
    """Бюджет остановки — 30 секунд, обработка длится до 5: прогон обязан
    доиграть, а не оборваться посередине."""
    created = await create(client, webhook_url=SINK_URL)
    payment_id = created["payment_id"]

    # дать consumer'у забрать сообщение и уйти в шлюз
    await asyncio.sleep(3)
    subprocess.run(["docker", "compose", "stop", "consumer"], check=True, capture_output=True)
    try:
        payment = (
            await client.get(f"/api/v1/payments/{payment_id}", headers={"X-API-Key": API_KEY})
        ).json()
        # либо прогон доиграл, либо платёж не тронут — но не «в процессе»
        assert payment["status"] in TERMINAL or payment["processed_at"] is None
    finally:
        subprocess.run(["docker", "compose", "start", "consumer"], check=True, capture_output=True)

    assert (await until(lambda: _settled_or_none(client, payment_id)))["status"] in TERMINAL


async def _settled_or_none(client: httpx.AsyncClient, payment_id: str) -> dict[str, Any] | None:
    payment = (
        await client.get(f"/api/v1/payments/{payment_id}", headers={"X-API-Key": API_KEY})
    ).json()
    return payment if payment["status"] in TERMINAL else None


async def test_the_consumer_never_touched_the_database_before_the_migrations() -> None:
    """Схему создаёт api, consumer стартует следом: без ожидания он бьётся
    в несуществующие таблицы."""
    logs = subprocess.run(
        ["docker", "compose", "logs", "--no-color", "consumer"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout

    assert "does not exist" not in logs
    assert "UndefinedTable" not in logs
