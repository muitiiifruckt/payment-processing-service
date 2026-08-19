from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from app.application.notify import WebhookRejectedError, WebhookUnavailableError
from app.infrastructure.config import settings
from app.infrastructure.webhook import HttpWebhookSender

URL = "https://receiver.test/hook"
PAYLOAD = {"payment_id": "p", "status": "succeeded"}


@pytest.mark.parametrize("status", [500, 502, 503, 408, 429])
async def test_temporary_answers_ask_for_a_retry(status: int) -> None:
    with respx.mock:
        respx.post(URL).mock(return_value=httpx.Response(status))
        with pytest.raises(WebhookUnavailableError):
            await HttpWebhookSender().send(URL, PAYLOAD)


@pytest.mark.parametrize("status", [400, 403, 404, 422])
async def test_client_errors_are_final(status: int) -> None:
    with respx.mock:
        respx.post(URL).mock(return_value=httpx.Response(status))
        with pytest.raises(WebhookRejectedError):
            await HttpWebhookSender().send(URL, PAYLOAD)


@pytest.mark.parametrize("status", [200, 201, 204])
async def test_successful_answers_do_not_raise(status: int) -> None:
    with respx.mock:
        respx.post(URL).mock(return_value=httpx.Response(status))
        await HttpWebhookSender().send(URL, PAYLOAD)


async def test_retry_after_is_read_from_the_answer() -> None:
    with respx.mock:
        respx.post(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "12"}))
        with pytest.raises(WebhookUnavailableError) as error:
            await HttpWebhookSender().send(URL, PAYLOAD)

    assert error.value.retry_after == 12.0


async def test_unparseable_retry_after_falls_back_to_our_own_delay() -> None:
    with respx.mock:
        respx.post(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "soon"}))
        with pytest.raises(WebhookUnavailableError) as error:
            await HttpWebhookSender().send(URL, PAYLOAD)

    assert error.value.retry_after is None


async def test_redirect_is_not_followed() -> None:
    """webhook_url задаёт клиент: переход по 3xx увёл бы запрос на адрес,
    которого клиент не называл."""
    with respx.mock:
        redirect = respx.post(URL).mock(
            return_value=httpx.Response(302, headers={"Location": "https://elsewhere.test/hook"})
        )
        elsewhere = respx.post("https://elsewhere.test/hook").mock(return_value=httpx.Response(200))
        with pytest.raises(WebhookRejectedError):
            await HttpWebhookSender().send(URL, PAYLOAD)

    assert redirect.called
    assert not elsewhere.called


async def test_timeout_is_a_temporary_failure() -> None:
    with respx.mock:
        respx.post(URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
        with pytest.raises(WebhookUnavailableError):
            await HttpWebhookSender().send(URL, PAYLOAD)


class CountingStream(httpx.AsyncByteStream):
    """Считает, сколько байт у неё забрали: тело получателя нам не нужно,
    и вычитывать его целиком — подставляться под ответ произвольного размера."""

    def __init__(self, chunk: bytes, chunks: int) -> None:
        self._chunk = chunk
        self._chunks = chunks
        self.given = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self._chunks):
            self.given += len(self._chunk)
            yield self._chunk


async def test_an_oversized_answer_is_not_read_whole() -> None:
    limit = settings.webhook_max_response_bytes
    stream = CountingStream(b"x" * 8192, chunks=limit // 8192 * 4)

    with respx.mock:
        respx.post(URL).mock(return_value=httpx.Response(200, stream=stream))
        await HttpWebhookSender().send(URL, PAYLOAD)

    assert stream.given <= limit + 8192


async def test_a_stalled_body_after_a_successful_answer_is_still_a_delivery() -> None:
    """Получатель ответил 200 — событие у него принято. Обрыв на чтении тела,
    которое нам не нужно, не должен превращаться в повторную отправку."""

    class BreakingStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"x"
            raise httpx.ReadTimeout("тело оборвалось")

    with respx.mock:
        respx.post(URL).mock(return_value=httpx.Response(200, stream=BreakingStream()))
        await HttpWebhookSender().send(URL, PAYLOAD)


async def test_an_address_inside_the_perimeter_is_never_requested() -> None:
    """Запрет постоянный: повторять запрещённый адрес незачем, и уж точно
    не следует к нему обращаться, чтобы это выяснить."""
    with respx.mock:
        route = respx.post("http://127.0.0.1:5432/hook").mock(return_value=httpx.Response(200))
        with pytest.raises(WebhookRejectedError):
            await HttpWebhookSender().send("http://127.0.0.1:5432/hook", PAYLOAD)

    assert not route.called
