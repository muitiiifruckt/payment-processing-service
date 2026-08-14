class DomainError(Exception):
    """Нарушение правила предметной области."""


class InvalidTransition(DomainError):
    """Переход статуса, не разрешённый моделью состояний."""


class InvalidAmount(DomainError):
    """Сумма не удовлетворяет инвариантам платежа."""
