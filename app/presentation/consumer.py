import logging
from typing import Any

from faststream import FastStream
from faststream.rabbit import RabbitBroker

from app.application.ports import Clock, PaymentGateway

log = logging.getLogger(__name__)


def register_handlers(
    broker: RabbitBroker, *, gateway: PaymentGateway, clock: Clock, sessions: Any
) -> None:
    return None


def create_app() -> FastStream:
    raise NotImplementedError
