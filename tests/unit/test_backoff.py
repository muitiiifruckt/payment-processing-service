from app.application.backoff import RETRY_QUEUE_DELAYS, webhook_backoff


def test_webhook_backoff_grows_exponentially() -> None:
    """Три попытки доставки — значит две задержки между ними (RFC §6.2)."""
    assert [webhook_backoff(1), webhook_backoff(2)] == [1.0, 2.0]
