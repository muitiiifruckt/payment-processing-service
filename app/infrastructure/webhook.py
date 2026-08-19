import logging
from typing import Any

import httpx

from app.application.notify import WebhookRejectedError, WebhookUnavailableError
from app.infrastructure.config import settings
from app.infrastructure.webhook_targets import (
    ForbiddenTargetError,
    TargetUnresolvedError,
    ensure_allowed,
)

#: 408 и 429 — единственные 4xx, которые имеет смысл повторять (RFC §6.2)
RETRYABLE_CLIENT_ERRORS = frozenset({408, 429})

log = logging.getLogger(__name__)


def make_client() -> httpx.AsyncClient:
    """Переадресации не выполняются: адрес задаёт клиент, и второй хоп
    увёл бы запрос туда, куда он не просил."""
    return httpx.AsyncClient(timeout=settings.webhook_timeout_seconds, follow_redirects=False)


class HttpWebhookSender:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def send(self, url: str, payload: dict[str, Any]) -> None:
        if self._client is not None:
            await self._post(self._client, url, payload)
            return
        async with make_client() as client:
            await self._post(client, url, payload)

    async def _post(self, client: httpx.AsyncClient, url: str, payload: dict[str, Any]) -> None:
        try:
            await ensure_allowed(url, allowed_hosts=settings.webhook_allowed_host_list)
        except ForbiddenTargetError as error:
            # отказ постоянный: адрес не станет разрешённым от повтора
            raise WebhookRejectedError(str(error)) from error
        except TargetUnresolvedError as error:
            # резолвер моргнул — к следующей попытке имя может разрешиться
            raise WebhookUnavailableError(str(error)) from error

        request = client.build_request("POST", url, json=payload)
        try:
            response = await client.send(request, stream=True)
        except httpx.HTTPError as error:
            # обрыв, отказ DNS, истёкшее ожидание — получателя сейчас нет,
            # но он может появиться к следующей попытке
            raise WebhookUnavailableError(f"{type(error).__name__}: {error}") from error

        # исход решает код ответа: он уже известен, а тело нам не нужно.
        # Обрыв на чтении тела после 200 — не повод отправлять событие второй раз
        await _drain(response)
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


async def _drain(response: httpx.Response) -> None:
    """Тело получателя нам не нужно, но соединение надо освободить. Читаем
    потоком и не дальше лимита: иначе ответ произвольного размера съест память."""
    read = 0
    try:
        async for chunk in response.aiter_bytes():
            read += len(chunk)
            if read >= settings.webhook_max_response_bytes:
                break
    except httpx.HTTPError:
        log.debug("тело ответа получателя дочитать не удалось", exc_info=True)
    finally:
        await response.aclose()


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
