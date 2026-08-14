from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.errors import InvalidTransitionError
from app.domain.money import Money
from app.domain.status import PaymentStatus

TERMINAL_STATUSES = frozenset({PaymentStatus.SUCCEEDED, PaymentStatus.FAILED})


@dataclass(slots=True)
class Payment:
    payment_id: UUID
    amount: Money
    created_at: datetime
    idempotency_key: str
    request_hash: str
    status: PaymentStatus = PaymentStatus.PENDING
    processed_at: datetime | None = None

    def mark_succeeded(self, now: datetime) -> None:
        self._settle(PaymentStatus.SUCCEEDED, now)

    def mark_failed(self, now: datetime) -> None:
        self._settle(PaymentStatus.FAILED, now)

    def _settle(self, status: PaymentStatus, now: datetime) -> None:
        if self.status in TERMINAL_STATUSES:
            raise InvalidTransitionError(f"платёж {self.payment_id} уже в статусе {self.status}")
        self.status = status
        self.processed_at = now
