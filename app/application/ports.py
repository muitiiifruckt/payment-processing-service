from datetime import datetime
from typing import Protocol
from uuid import UUID


class Clock(Protocol):
    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class IdGenerator(Protocol):
    def new_id(self) -> UUID: ...


class GatewayUnavailableError(Exception):
    """Отказ инфраструктуры шлюза, а не решение по платежу.

    Разница существенная: бизнес-отказ терминален, недоступность обязана
    привести к повтору с тем же исходом позже."""


class PaymentGateway(Protocol):
    async def process(self, payment_id: UUID) -> bool: ...
