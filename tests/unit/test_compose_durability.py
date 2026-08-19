from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
DATA_DIRS = {"postgres": "/var/lib/postgresql/data", "rabbitmq": "/var/lib/rabbitmq"}


def compose() -> tuple[dict[str, dict], dict]:
    parsed = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return parsed["services"], parsed.get("volumes") or {}


def test_stateful_services_keep_their_data_on_named_volumes() -> None:
    """Слой контейнера переживает restart, но не пересоздание. Без тома
    durable-очереди и persistent-сообщения не значат ничего."""
    parsed, volumes = compose()

    for service, data_dir in DATA_DIRS.items():
        mounts = parsed[service].get("volumes") or []
        named = [mount for mount in mounts if mount.endswith(f":{data_dir}")]
        assert named, f"{service} хранит данные в слое контейнера"
        assert named[0].split(":")[0] in volumes, f"том {service} не объявлен"
