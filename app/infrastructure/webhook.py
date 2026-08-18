from typing import Any

import httpx

from app.application.notify import WebhookUnavailableError


class HttpWebhookSender:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def send(self, url: str, payload: dict[str, Any]) -> None:
        raise WebhookUnavailableError("не реализовано")
