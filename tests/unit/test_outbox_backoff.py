from app.application.policy import OUTBOX_BASE_DELAY, OUTBOX_MAX_DELAY, outbox_backoff


def test_deferral_grows_exponentially_from_the_base_delay() -> None:
    assert [outbox_backoff(n) for n in range(4)] == [
        OUTBOX_BASE_DELAY,
        OUTBOX_BASE_DELAY * 2,
        OUTBOX_BASE_DELAY * 4,
        OUTBOX_BASE_DELAY * 8,
    ]


def test_deferral_never_exceeds_the_ceiling() -> None:
    assert outbox_backoff(100) == OUTBOX_MAX_DELAY
