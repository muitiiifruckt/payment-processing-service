import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app

PAYLOAD = {"event_id": "e", "payment_id": "p", "status": "succeeded"}


async def client_for(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> AsyncClient:
    monkeypatch.setattr("app.infrastructure.config.settings.enable_webhook_sink", enabled)
    app = create_app()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_the_sink_accepts_a_webhook_when_it_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with await client_for(monkeypatch, enabled=True) as client:
        response = await client.post("/__sink__/webhook", json=PAYLOAD)
        received = await client.get("/__sink__/webhook")

    assert response.status_code == 204
    assert received.json() == [PAYLOAD]


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
