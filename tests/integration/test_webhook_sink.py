import pytest
from httpx import ASGITransport, AsyncClient

from app.infrastructure.config import settings
from app.main import create_app
from app.presentation.api.sink import received

PAYLOAD = {"event_id": "e", "payment_id": "p", "status": "succeeded"}
KEY = {"X-API-Key": settings.api_key}


@pytest.fixture(autouse=True)
def empty_sink() -> None:
    """Приёмник живёт в памяти модуля: без очистки тест зависит от соседей."""
    received.clear()


async def client_for(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> AsyncClient:
    monkeypatch.setattr("app.infrastructure.config.settings.enable_webhook_sink", enabled)
    app = create_app()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_the_sink_accepts_a_webhook_when_it_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with await client_for(monkeypatch, enabled=True) as client:
        response = await client.post("/__sink__/webhook", json=PAYLOAD)
        listing = await client.get("/__sink__/webhook", headers=KEY)

    assert response.status_code == 204
    assert listing.json() == [PAYLOAD]


async def test_the_sink_is_absent_when_it_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Демонстрационный приёмник не должен существовать в обычном запуске."""
    async with await client_for(monkeypatch, enabled=False) as client:
        response = await client.post("/__sink__/webhook", json=PAYLOAD)

    assert response.status_code == 404


async def test_the_sink_does_not_appear_in_the_public_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.infrastructure.config.settings.enable_webhook_sink", True)

    schema = create_app().openapi()

    assert not [path for path in schema["paths"] if "sink" in path]


async def test_reading_the_sink_requires_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Наружу это содержимое чужих платежей: суммы, статусы, идентификаторы."""
    async with await client_for(monkeypatch, enabled=True) as client:
        response = await client.get("/__sink__/webhook")

    assert response.status_code == 401


async def test_the_flaky_endpoint_refuses_the_asked_number_of_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Приёмник, отвечающий отказом заданное число раз, нужен сквозному
    тесту повторов: своего эндпоинта с управляемым отказом иначе нет."""
    async with await client_for(monkeypatch, enabled=True) as client:
        first = await client.post("/__sink__/flaky/2", json=PAYLOAD)
        second = await client.post("/__sink__/flaky/2", json=PAYLOAD)
        third = await client.post("/__sink__/flaky/2", json=PAYLOAD)
        listing = await client.get("/__sink__/webhook", headers=KEY)

    assert [first.status_code, second.status_code, third.status_code] == [503, 503, 204]
    assert listing.json() == [PAYLOAD]
