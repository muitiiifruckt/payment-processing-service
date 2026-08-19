import os
import subprocess
from collections.abc import Iterator

import pytest

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("E2E_API_KEY", "local-dev-key")
SINK_BASE = os.getenv("E2E_SINK_BASE", "http://api:8000/__sink__")
AMQP_URL = os.getenv("E2E_AMQP_URL", "amqp://guest:guest@localhost:5672/")
TERMINAL = frozenset({"succeeded", "failed"})

#: Корень проекта: тесты не обязаны запускаться из него
PROJECT_ROOT = os.fspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


def compose(*args: str) -> str:
    result = subprocess.run(
        ["docker", "compose", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=PROJECT_ROOT,
    )
    return result.stdout


@pytest.fixture
def stopped_consumer() -> Iterator[None]:
    """Останавливает обработчик и обязательно поднимает обратно: иначе
    один упавший тест уносит с собой все следующие."""
    compose("stop", "consumer")
    try:
        yield
    finally:
        compose("start", "consumer")


def logs_of(service: str) -> str:
    """Логи только текущего запуска контейнера: они копятся с его создания,
    включая прошлые прогоны, и проверка по всему объёму зелёной не станет."""
    container = compose("ps", "-q", service).strip().splitlines()[0]
    started = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", container],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()
    return compose("logs", "--no-color", "--since", started, service)
