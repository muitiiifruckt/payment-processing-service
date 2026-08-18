from typing import Any

import httpx

from app.application.notify import WebhookRejectedError, WebhookUnavailableError
from app.infrastructure.config import settings

#: 408 и 429 — единственные 4xx, которые имеет смысл повторять (RFC §6.2)
RETRYABLE_CLIENT_ERRORS = frozenset({408, 429})


class HttpWebhookSender:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def send(self, url: str, payload: dict[str, Any]) -> None:
        if self._client is not None:
            await self._post(self._client, url, payload)
            return
        async with httpx.AsyncClient(
            timeout=settings.webhook_timeout_seconds, follow_redirects=False
        ) as client:
            await self._post(client, url, payload)

    async def _post(self, client: httpx.AsyncClient, url: str, payload: dict[str, Any]) -> None:
        response = await client.post(url, json=payload)
        if response.status_code < 400:
            return
        if response.status_code >= 500 or response.status_code in RETRYABLE_CLIENT_ERRORS:
            raise WebhookUnavailableError(f"получатель ответил {response.status_code}")
        raise WebhookRejectedError(f"получатель ответил {response.status_code}")
