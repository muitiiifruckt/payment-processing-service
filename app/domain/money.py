from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.domain.errors import InvalidAmountError

#: Совпадает с numeric(20, 4) в таблице
MAX_SCALE = 4
MAX_AMOUNT = Decimal(10) ** 16
#: Наружу деньги всегда строкой и не короче двух знаков
OUTPUT_SCALE = Decimal("0.01")


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

    @property
    def formatted(self) -> str:
        """Представление для API и webhook."""
        # numeric(20,4) возвращает масштаб колонки, поэтому сначала снимаем
        # незначащие нули, и только потом добиваем до двух знаков. Обратный
        # порядок округлял бы 1.005 до 1.00 — сумма, которой в базе нет
        trimmed = self.amount.normalize()
        exponent = trimmed.as_tuple().exponent
        if not isinstance(exponent, int) or exponent > -2:
            trimmed = trimmed.quantize(OUTPUT_SCALE)
        return format(trimmed, "f")
