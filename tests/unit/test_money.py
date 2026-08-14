from decimal import Decimal

import pytest

from app.domain.errors import InvalidAmount
from app.domain.money import Currency, Money


def test_zero_amount_is_rejected() -> None:
    with pytest.raises(InvalidAmount):
        Money(Decimal("0"), Currency.RUB)
