import subprocess
import sys

#: Отдельный процесс: соседние тесты уже импортировали consumer, и в общем
#: sys.modules брокер будет присутствовать всегда
SCRIPT = (
    "import sys, app.main;"
    "app.main.create_app();"
    "print(sorted(m for m in sys.modules if m.split('.')[0] in {'faststream', 'aio_pika'}))"
)


def test_the_api_does_not_pull_in_the_broker() -> None:
    """Публикация — дело relay. Обращение к брокеру прямо из обработчика
    сделало бы outbox бессмысленным (RFC §2.4), а собрать такое обращение
    нельзя, не притащив в процесс сам брокер."""
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    assert result.stdout.strip() == "[]"
