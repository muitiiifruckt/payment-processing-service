from decimal import Decimal

import pytest

from app.domain.errors import InvalidAmountError
from app.domain.money import Currency, Money


def test_zero_amount_is_rejected() -> None:
    with pytest.raises(InvalidAmountError):
        Money(Decimal("0"), Currency.RUB)


def test_negative_amount_is_rejected() -> None:
    with pytest.raises(InvalidAmountError):
        Money(Decimal("-1.00"), Currency.RUB)


def test_currency_outside_the_allowed_set_is_rejected() -> None:
    with pytest.raises(ValueError):
        Currency("GBP")


def test_amounts_differing_only_in_scale_are_equal() -> None:
    assert Money(Decimal("100"), Currency.RUB) == Money(Decimal("100.00"), Currency.RUB)


def test_same_amount_in_different_currencies_is_not_equal() -> None:
    assert Money(Decimal("100.00"), Currency.RUB) != Money(Decimal("100.00"), Currency.USD)


def test_amount_with_too_many_decimal_places_is_rejected() -> None:
    with pytest.raises(InvalidAmountError):
        Money(Decimal("0.00001"), Currency.RUB)


def test_amount_beyond_the_column_range_is_rejected() -> None:
    with pytest.raises(InvalidAmountError):
        Money(Decimal("1e17"), Currency.RUB)


def test_non_finite_amount_is_rejected() -> None:
    with pytest.raises(InvalidAmountError):
        Money(Decimal("NaN"), Currency.RUB)
