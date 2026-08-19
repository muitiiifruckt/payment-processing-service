import asyncio
import uuid
from typing import Any

import aio_pika
import httpx
import pytest

from app.infrastructure.broker import topology
from tests.e2e.conftest import (
    AMQP_URL,
    API_KEY,
    BASE_URL,
    SINK_BASE,
    TERMINAL,
    compose,
    logs_of,
)

SINK_URL = f"{SINK_BASE}/webhook"


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
    """Окружение поднялось с нуля. Проба здесь не показатель — SELECT 1
    проходит и на пустой базе; показатель тот, что платёж создаётся,
    то есть таблицы существуют и версия схемы накачена."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"

    created = await create(client)

    assert created["status"] == "pending"
    assert compose("exec", "-T", "api", "alembic", "current").strip().endswith("(head)")


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
    flaky = f"{SINK_BASE}/flaky/2"
    created = await create(client, webhook_url=flaky)
    payment_id = created["payment_id"]

    async def delivered() -> dict[str, Any] | None:
        received = (await client.get("/__sink__/webhook", headers={"X-API-Key": API_KEY})).json()
        matching = [event for event in received if event["payment_id"] == payment_id]
        return matching[0] if matching else None

    await until(delivered)

    counts = (await client.get("/__sink__/flaky", headers={"X-API-Key": API_KEY})).json()
    assert counts[payment_id] == 3


async def test_a_message_survives_a_broker_restart(client: httpx.AsyncClient) -> None:
    """Сообщения persistent, очередь durable: перезапуск брокера не должен
    стирать то, что уже лежит в DLQ и ждёт разбора."""

    async def depth() -> int:
        """Пассивное объявление: очередь уже существует, нам нужна её глубина."""
        connection = await aio_pika.connect_robust(AMQP_URL)
        async with connection:
            channel = await connection.channel()
            declared = await channel.declare_queue(topology.DLQ_KEY, passive=True)
            return int(declared.declaration_result.message_count or 0)

    # запрещённый адрес — постоянный отказ, сообщение уезжает в DLQ и там лежит
    await create(client, webhook_url="http://10.0.0.1/hook")
    before = await until(lambda: _at_least_one(depth()))

    compose("restart", "rabbitmq")

    # брокер поднимается не мгновенно: первые попытки упрутся в сброс соединения
    assert await until(lambda: _reachable(depth())) >= before


async def _at_least_one(pending: Any) -> int | None:
    depth = await pending
    return depth if depth >= 1 else None


async def _reachable(pending: Any) -> int | None:
    try:
        return await pending
    except Exception:
        return None


async def test_stopping_the_consumer_does_not_leave_a_payment_half_done(
    client: httpx.AsyncClient, stopped_consumer: None
) -> None:
    """Бюджет остановки — 30 секунд, обработка длится до 5: платёж не должен
    остаться в промежуточном состоянии, пока обработчик выключен."""
    created = await create(client, webhook_url=SINK_URL)
    payment_id = created["payment_id"]

    payment = (
        await client.get(f"/api/v1/payments/{payment_id}", headers={"X-API-Key": API_KEY})
    ).json()

    # обработчик выключен, платёж его не дождался — он обязан остаться нетронутым
    assert payment["status"] == "pending"
    assert payment["processed_at"] is None

    # и доиграть, когда обработчик вернётся: остановка не потеряла сообщение
    compose("start", "consumer")
    assert (await until(lambda: _settled(client, payment_id)))["status"] in TERMINAL


async def _settled(client: httpx.AsyncClient, payment_id: str) -> dict[str, Any] | None:
    payment = (
        await client.get(f"/api/v1/payments/{payment_id}", headers={"X-API-Key": API_KEY})
    ).json()
    return payment if payment["status"] in TERMINAL else None


async def test_the_consumer_never_touched_the_database_before_the_migrations() -> None:
    """Схему создаёт api, consumer стартует следом: без ожидания он бьётся
    в несуществующие таблицы."""
    logs = logs_of("consumer")

    assert "does not exist" not in logs
    assert "UndefinedTable" not in logs
