from app.application.policy import CLAIM_LEASE, RETRY_QUEUE_DELAYS, webhook_backoff


def test_webhook_backoff_grows_exponentially() -> None:
    """Три попытки доставки — значит две задержки между ними (RFC §6.2)."""
    assert [webhook_backoff(1), webhook_backoff(2)] == [1.0, 2.0]


def test_retry_queue_delays_are_fixed() -> None:
    assert RETRY_QUEUE_DELAYS == (2, 4, 8)


def test_retry_queue_delays_do_not_coincide_with_webhook_backoff() -> None:
    """Механизмы разные: совпадение чисел маскировало бы их смешение (RFC §6)."""
    webhook_delays = [webhook_backoff(attempt) for attempt in (1, 2)]

    assert list(RETRY_QUEUE_DELAYS) != webhook_delays


def test_claim_lease_expires_before_the_retries_run_out() -> None:
    """Иначе платёж, чей обработчик умер, не захватить ни на одном повторе."""
    assert CLAIM_LEASE.total_seconds() < sum(RETRY_QUEUE_DELAYS)
