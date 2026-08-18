from typing import Any, Protocol
from uuid import UUID

from app.domain.payment import Payment


class WebhookSender(Protocol):
    async def send(self, url: str, payload: dict[str, Any]) -> None: ...


def webhook_payload(payment: Payment, *, event_id: UUID) -> dict[str, Any]:
    return {}
