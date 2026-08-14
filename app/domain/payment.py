from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.money import Money
from app.domain.status import PaymentStatus


@dataclass(slots=True)
class Payment:
    payment_id: UUID
    amount: Money
    created_at: datetime
    status: PaymentStatus = PaymentStatus.PENDING
    processed_at: datetime | None = None
