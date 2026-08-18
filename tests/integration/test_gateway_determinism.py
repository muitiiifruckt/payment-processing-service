import os
import subprocess
import sys
from uuid import UUID


def test_outcome_survives_a_restart_of_the_process() -> None:
    # встроенный hash() солится на каждый запуск: повтор после рестарта
    # обработчика дал бы другой исход по тому же платежу
    fixed = UUID("00000000-0000-0000-0000-000000000001")
    script = (
        "from uuid import UUID;"
        "from app.infrastructure.gateway import outcome_for, duration_for;"
        f"print(outcome_for(UUID('{fixed}')), duration_for(UUID('{fixed}')))"
    )

    runs = {
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout.strip()
        for seed in ("1", "2")
    }

    assert len(runs) == 1
