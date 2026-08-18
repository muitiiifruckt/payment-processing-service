from typing import Any, Protocol
from uuid import UUID

from app.domain.payment import Payment


class WebhookSender(Protocol):
    async def send(self, url: str, payload: dict[str, Any]) -> None: ...


def webhook_payload(payment: Payment, *, event_id: UUID) -> dict[str, Any]:
    assert payment.processed_at is not None  # webhook уходит только по обработанному
    return {
        "event_id": str(event_id),
        "payment_id": str(payment.payment_id),
        "status": payment.status.value,
        "amount": payment.amount.formatted,
        "currency": payment.amount.currency.value,
        "processed_at": payment.processed_at.isoformat(),
    }
