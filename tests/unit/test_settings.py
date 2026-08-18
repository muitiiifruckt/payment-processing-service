import pytest
from pydantic import ValidationError

from app.infrastructure.config import Settings


def test_unknown_forced_gateway_outcome_is_rejected_at_startup() -> None:
    """Опечатка в GATEWAY_FORCE_OUTCOME иначе означает «все платежи неуспешны»:
    всё, что не равно succeeded, молча трактуется как отказ."""
    with pytest.raises(ValidationError):
        Settings(api_key="k", gateway_force_outcome="success")


@pytest.mark.parametrize("outcome", ["succeeded", "failed", None])
def test_supported_forced_outcomes_are_accepted(outcome: str | None) -> None:
    assert Settings(api_key="k", gateway_force_outcome=outcome).gateway_force_outcome == outcome


def test_an_empty_forced_outcome_reads_as_absent() -> None:
    """compose подставляет пустую строку для незаданной переменной, и без
    этого сервис не поднимется вовсе."""
    assert Settings(api_key="k", gateway_force_outcome="").gateway_force_outcome is None
