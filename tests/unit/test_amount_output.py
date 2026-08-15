from decimal import Decimal

import pytest

from app.presentation.api.schemas import format_amount


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        (Decimal("100"), "100.00"),
        (Decimal("10.5"), "10.50"),
        (Decimal("0.01"), "0.01"),
        # колонка numeric(20,4) — три и четыре знака хранятся и обязаны
        # доезжать до клиента, а не округляться до другой суммы
        (Decimal("1.005"), "1.005"),
        (Decimal("1.0055"), "1.0055"),
    ],
)
def test_amount_is_shown_without_losing_stored_precision(stored: Decimal, shown: str) -> None:
    assert format_amount(stored) == shown
