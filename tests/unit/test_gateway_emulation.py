import asyncio
import os
import subprocess
import sys
from uuid import UUID, uuid4

from app.infrastructure.gateway import EmulatedGateway, duration_for, outcome_for

MIN_DURATION = 2.0
MAX_DURATION = 5.0


def test_outcome_is_the_same_for_the_same_payment() -> None:
    payment_id = uuid4()

    assert outcome_for(payment_id) == outcome_for(payment_id)


def test_duration_is_the_same_for_the_same_payment() -> None:
    payment_id = uuid4()

    assert duration_for(payment_id) == duration_for(payment_id)


def test_duration_stays_inside_the_declared_range() -> None:
    durations = [duration_for(uuid4()) for _ in range(500)]

    assert all(MIN_DURATION <= value <= MAX_DURATION for value in durations)
    # вырожденное отображение прошло бы проверку диапазона
    assert len(set(durations)) > 50


def test_success_rate_is_about_ninety_percent() -> None:
    outcomes = [outcome_for(uuid4()) for _ in range(5000)]

    share = sum(outcomes) / len(outcomes)
    assert 0.88 <= share <= 0.92


def test_outcome_does_not_depend_on_call_order() -> None:
    ids = [uuid4() for _ in range(100)]

    straight = [outcome_for(payment_id) for payment_id in ids]
    reversed_order = [outcome_for(payment_id) for payment_id in reversed(ids)]

    assert straight == list(reversed(reversed_order))


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


class NoWaitClock:
    def now(self) -> None: ...

    async def sleep(self, seconds: float) -> None: ...


def test_forced_outcome_overrides_the_one_derived_from_the_id() -> None:
    payment_id = next(pid for pid in (uuid4() for _ in range(100)) if outcome_for(pid))

    forced = asyncio.run(EmulatedGateway(NoWaitClock(), "failed").process(payment_id))  # type: ignore[arg-type]

    assert outcome_for(payment_id) is True
    assert forced is False
