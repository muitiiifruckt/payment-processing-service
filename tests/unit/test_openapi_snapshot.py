import importlib
import json
import os
from pathlib import Path

import pytest

SNAPSHOT_PATH = Path(__file__).parent / "openapi.json"

# Совпадают со значениями по умолчанию в Settings — фиксируем явно, чтобы
# снапшот не зависел от .env на машине, где гоняется тест.
ENV_OVERRIDES = {
    "DATABASE_URL": "postgresql+asyncpg://payments:payments@localhost:5432/payments",
    "RABBITMQ_URL": "amqp://guest:guest@localhost:5672/",
    "API_KEY": "local-dev-key",
    "DESCRIPTION_MAX_LENGTH": "512",
    "METADATA_MAX_BYTES": "8192",
    "WEBHOOK_TIMEOUT_SECONDS": "5.0",
    "WEBHOOK_MAX_RESPONSE_BYTES": "65536",
    "ENABLE_WEBHOOK_SINK": "true",
}

# Порядок важен: settings читаются на импорте модулей ниже по цепочке.
RELOAD_MODULES = (
    "app.infrastructure.config",
    "app.presentation.api.schemas",
    "app.presentation.api.deps",
    "app.presentation.api.errors",
    "app.presentation.api.routes",
    "app.main",
)


@pytest.fixture
def openapi_schema(monkeypatch: pytest.MonkeyPatch) -> dict:
    for key, value in ENV_OVERRIDES.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GATEWAY_FORCE_OUTCOME", raising=False)

    modules = [importlib.reload(importlib.import_module(name)) for name in RELOAD_MODULES]
    main_module = modules[-1]
    return main_module.create_app().openapi()


def _dump(schema: dict) -> str:
    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def test_openapi_matches_snapshot(openapi_schema: dict) -> None:
    actual = _dump(openapi_schema)

    if os.getenv("UPDATE_SNAPSHOTS"):
        SNAPSHOT_PATH.write_text(actual, encoding="utf-8")
        pytest.skip("снапшот обновлён (UPDATE_SNAPSHOTS=1)")

    if not SNAPSHOT_PATH.exists():
        pytest.fail(f"эталон {SNAPSHOT_PATH} отсутствует — сгенерируйте с UPDATE_SNAPSHOTS=1")

    expected = SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "OpenAPI-контракт изменился. Если это осознанно — обновите эталон:\n"
        "UPDATE_SNAPSHOTS=1 uv run pytest tests/unit/test_openapi_snapshot.py"
    )
