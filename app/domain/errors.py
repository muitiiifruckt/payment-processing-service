class DomainError(Exception):
    """Нарушение правила предметной области."""


class InvalidTransitionError(DomainError):
    """Переход статуса, не разрешённый моделью состояний."""


class InvalidAmountError(DomainError):
    """Сумма не удовлетворяет инвариантам платежа."""
