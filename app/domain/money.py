from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.domain.errors import InvalidAmountError

#: Совпадает с numeric(20, 4) в таблице
MAX_SCALE = 4
MAX_AMOUNT = Decimal(10) ** 16


class Currency(StrEnum):
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not self.amount.is_finite():
            raise InvalidAmountError(f"сумма должна быть конечной, получено {self.amount}")
        if self.amount <= 0:
            raise InvalidAmountError(f"сумма должна быть положительной, получено {self.amount}")
        # границы колонки numeric(20, 4): без них Postgres округлит 0.00001 в ноль
        # и уронит CHECK, то есть отдаст 500 там, где место ошибке валидации
        exponent = self.amount.as_tuple().exponent
        assert isinstance(exponent, int)  # is_finite выше исключает 'n' / 'N' / 'F'
        if -exponent > MAX_SCALE:
            raise InvalidAmountError(f"не более {MAX_SCALE} знаков после запятой: {self.amount}")
        if self.amount >= MAX_AMOUNT:
            raise InvalidAmountError(f"сумма должна быть меньше {MAX_AMOUNT}: {self.amount}")
