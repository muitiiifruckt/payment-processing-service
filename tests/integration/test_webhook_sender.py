import httpx
import pytest
import respx

from app.application.notify import WebhookRejectedError, WebhookUnavailableError
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
