import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

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
    "GATEWAY_FORCE_OUTCOME": "",
}

# Отдельный процесс, а не importlib.reload: перезагрузка модулей подменяет
# settings и зависимости на весь прогон, и соседние тесты начинают ходить
# в другое приложение.
SCRIPT = "import json;from app.main import create_app;print(json.dumps(create_app().openapi()))"


@pytest.fixture
def openapi_schema() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, **ENV_OVERRIDES},
        check=True,
    )
    schema: dict[str, Any] = json.loads(result.stdout)
    return schema


def normalize(schema: dict[str, Any]) -> dict[str, Any]:
    """Формулировки стандартных статусов приходят из stdlib и меняются
    от версии Python — контракт от этого не меняется."""
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            for code, response in operation.get("responses", {}).items():
                if code != "200":
                    response.pop("description", None)
    return schema


def dump(schema: dict[str, Any]) -> str:
    return json.dumps(normalize(schema), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def test_openapi_matches_snapshot(openapi_schema: dict[str, Any]) -> None:
    actual = dump(openapi_schema)

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


def test_the_demo_sink_is_absent_from_the_public_schema(openapi_schema: dict[str, Any]) -> None:
    assert not [path for path in openapi_schema["paths"] if "sink" in path]
