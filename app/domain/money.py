from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.domain.errors import InvalidAmount


class Currency(StrEnum):
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise InvalidAmount(f"сумма должна быть положительной, получено {self.amount}")
