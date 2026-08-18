from typing import Any
from uuid import uuid4

import pytest

from app.application.create_payment import PAYMENT_CREATED
from app.application.policy import MAX_HANDLER_RUNS
from app.application.process_payment import PermanentError, TransientError
from app.infrastructure.broker import topology
from app.presentation.failure_routing import PERMANENT, TRANSIENT, route_failure

EVENT = {"event_id": str(uuid4()), "payment_id": str(uuid4())}


class Recorder:
    """Перехватывает публикации брокера: нас интересует, куда ушло сообщение."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def publish(self, message: Any, **kwargs: Any) -> None:
        self.sent.append({"message": message, **kwargs})


@pytest.fixture
def broker() -> Any:
    return Recorder()


async def route(broker: Recorder, event: dict[str, Any], **kwargs: Any) -> None:
    await route_failure(broker, event, event_type=PAYMENT_CREATED, **kwargs)  # type: ignore[arg-type]


async def test_a_temporary_failure_on_the_first_run_goes_to_the_first_retry_queue(
    broker: Recorder,
) -> None:
    await route(
        broker,
        EVENT,
        attempt=0,
        error=TransientError("шлюз недоступен"),
        kind=TRANSIENT,
    )

    assert len(broker.sent) == 1
    assert broker.sent[0]["routing_key"] == topology.retry_key(1)
    assert broker.sent[0]["headers"]["x-attempt"] == 1


async def test_the_attempt_number_grows_from_retry_to_retry(broker: Recorder) -> None:
    for attempt in range(MAX_HANDLER_RUNS - 1):
        await route(broker, EVENT, attempt=attempt, error=TransientError("ещё раз"), kind=TRANSIENT)

    assert [sent["routing_key"] for sent in broker.sent] == [
        topology.retry_key(1),
        topology.retry_key(2),
        topology.retry_key(3),
    ]
    assert [sent["headers"]["x-attempt"] for sent in broker.sent] == [1, 2, 3]


async def test_the_fourth_failed_run_goes_to_the_dead_letter_queue(broker: Recorder) -> None:
    await route(
        broker,
        EVENT,
        attempt=MAX_HANDLER_RUNS - 1,
        error=TransientError("не вышло и в четвёртый раз"),
        kind=TRANSIENT,
    )

    assert broker.sent[0]["routing_key"] == topology.DLQ_KEY
    assert broker.sent[0]["exchange"] is topology.dlx_exchange


async def test_a_permanent_failure_skips_the_retries(broker: Recorder) -> None:
    await route(
        broker,
        EVENT,
        attempt=0,
        error=PermanentError("платёж не найден"),
        kind=PERMANENT,
    )

    assert broker.sent[0]["routing_key"] == topology.DLQ_KEY


async def test_the_dead_letter_carries_the_reason_class_payment_and_run_number(
    broker: Recorder,
) -> None:
    await route(
        broker,
        EVENT,
        attempt=2,
        error=PermanentError("платёж не найден"),
        kind=PERMANENT,
    )

    headers = broker.sent[0]["headers"]
    assert headers["x-failure-class"] == PERMANENT
    assert "платёж не найден" in headers["x-failure-reason"]
    assert headers["x-payment-id"] == EVENT["payment_id"]
    assert headers["x-attempt"] == 2


async def test_the_event_id_survives_both_the_retry_and_the_dead_letter(
    broker: Recorder,
) -> None:
    await route(
        broker,
        EVENT,
        attempt=0,
        error=TransientError("раз"),
        kind=TRANSIENT,
    )
    await route(
        broker,
        EVENT,
        attempt=MAX_HANDLER_RUNS - 1,
        error=TransientError("два"),
        kind=TRANSIENT,
    )

    assert [sent["message"]["event_id"] for sent in broker.sent] == [EVENT["event_id"]] * 2
