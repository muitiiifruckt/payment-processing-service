import asyncio
import logging
from typing import Any
from uuid import UUID

from faststream import Context, FastStream
from faststream.rabbit import RabbitBroker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.create_payment import PAYMENT_CREATED
from app.application.ports import Clock, PaymentGateway
from app.application.process_payment import PermanentError, process_payment
from app.application.publish_outbox import relay_forever
from app.infrastructure.broker import topology
from app.infrastructure.broker.broker import (
    RabbitEventPublisher,
    declare_topology,
    make_broker,
)
from app.infrastructure.clock import SystemClock
from app.infrastructure.config import settings
from app.infrastructure.db.session import dispose, session_factory
from app.infrastructure.gateway import EmulatedGateway

log = logging.getLogger(__name__)


def register_handlers(
    broker: RabbitBroker,
    *,
    gateway: PaymentGateway,
    clock: Clock,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    @broker.subscriber(topology.payments_new, topology.payments_exchange)
    async def on_payment_created(
        event: dict[str, Any],
        event_type: str = Context("message.headers.x-event-type", default=""),
    ) -> None:
        if event_type != PAYMENT_CREATED:
            raise PermanentError(f"неизвестный тип события: {event_type}")

        raw = event.get("payment_id")
        if not isinstance(raw, str):
            raise PermanentError("в событии нет payment_id")
        try:
            payment_id = UUID(raw)
        except ValueError as error:
            raise PermanentError(f"payment_id не разбирается: {raw}") from error

        await process_payment(sessions, payment_id, gateway=gateway, clock=clock)


def build_app(
    broker: RabbitBroker,
    *,
    gateway: PaymentGateway,
    clock: Clock,
    sessions: async_sessionmaker[AsyncSession],
) -> FastStream:
    register_handlers(broker, gateway=gateway, clock=clock, sessions=sessions)

    app = FastStream(broker)
    relay: list[asyncio.Task[None]] = []

    @app.after_startup
    async def start_relay() -> None:
        await declare_topology(broker)
        relay.append(
            asyncio.create_task(relay_forever(sessions, RabbitEventPublisher(broker), clock))
        )

    @app.on_shutdown
    async def stop_relay() -> None:
        for task in relay:
            task.cancel()
        await asyncio.gather(*relay, return_exceptions=True)
        relay.clear()

    return app


def create_app() -> FastStream:
    """Фабрика, а не модульный app: импорт модуля не должен поднимать пул
    к БД и соединение с брокером.

    Запуск: faststream run app.presentation.consumer:create_app --factory
    """
    clock = SystemClock()
    app = build_app(
        make_broker(),
        gateway=EmulatedGateway(clock, settings.gateway_force_outcome),
        clock=clock,
        sessions=session_factory(),
    )

    @app.on_shutdown
    async def close_database() -> None:
        await dispose()

    return app
