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
        try:
            response = await client.post(url, json=payload)
        except httpx.HTTPError as error:
            # обрыв, отказ DNS, истёкшее ожидание — получателя сейчас нет,
            # но он может появиться к следующей попытке
            raise WebhookUnavailableError(f"{type(error).__name__}: {error}") from error
        if response.status_code < 300:
            return
        if response.is_redirect:
            # переадресация не выполняется: адрес задан клиентом, и уход
            # на чужой хост по чужому же указанию — не доставка
            raise WebhookRejectedError(f"переадресация {response.status_code} не выполняется")
        if response.status_code >= 500 or response.status_code in RETRYABLE_CLIENT_ERRORS:
            raise WebhookUnavailableError(
                f"получатель ответил {response.status_code}",
                retry_after=_retry_after(response),
            )
        raise WebhookRejectedError(f"получатель ответил {response.status_code}")


def _retry_after(response: httpx.Response) -> float | None:
    """Только секунды: форма с датой требует доверия к часам получателя,
    а неразобранное значение лучше заменить собственной задержкой."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None
