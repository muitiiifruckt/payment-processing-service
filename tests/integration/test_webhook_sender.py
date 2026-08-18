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
