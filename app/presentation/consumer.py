import asyncio
import logging
from typing import Any
from uuid import UUID

from faststream import Context, FastStream
from faststream.rabbit import RabbitBroker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.create_payment import PAYMENT_CREATED
from app.application.notify import WebhookSender
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
from app.infrastructure.webhook import HttpWebhookSender, make_client
from app.presentation.failure_routing import PERMANENT, TRANSIENT, route_failure

log = logging.getLogger(__name__)


def register_handlers(
    broker: RabbitBroker,
    *,
    gateway: PaymentGateway,
    clock: Clock,
    sessions: async_sessionmaker[AsyncSession],
    sender: WebhookSender,
) -> None:
    @broker.subscriber(topology.payments_new, topology.payments_exchange)
    async def on_payment_created(
        event: dict[str, Any],
        event_type: str = Context("message.headers.x-event-type", default=""),
        attempt: int = Context("message.headers.x-attempt", default=0),
    ) -> None:
        try:
            await _handle(
                event,
                event_type,
                gateway=gateway,
                clock=clock,
                sessions=sessions,
                sender=sender,
            )
        except PermanentError as error:
            await _route(broker, event, attempt, error, PERMANENT, event_type)
        except Exception as error:
            # умолчание в пользу повтора: неучтённая ошибка не должна
            # стоить сообщения (RFC §6.3)
            await _route(broker, event, attempt, error, TRANSIENT, event_type)


async def _route(
    broker: RabbitBroker,
    event: dict[str, Any],
    attempt: int,
    error: Exception,
    kind: str,
    event_type: str,
) -> None:
    """Отказ самой пересылки не глушится: исходное сообщение не подтверждается
    и уходит по страховочному dead-letter, а причина видна в логе."""
    try:
        await route_failure(
            broker, event, attempt=attempt, error=error, kind=kind, event_type=event_type
        )
    except Exception:
        log.exception("не удалось переслать сообщение по отказу %s", error)
        raise


async def _handle(
    event: dict[str, Any],
    event_type: str,
    *,
    gateway: PaymentGateway,
    clock: Clock,
    sessions: async_sessionmaker[AsyncSession],
    sender: WebhookSender,
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

    await process_payment(
        sessions,
        payment_id,
        gateway=gateway,
        clock=clock,
        sender=sender,
        event_id=_event_id(event),
    )


def build_app(
    broker: RabbitBroker,
    *,
    gateway: PaymentGateway,
    clock: Clock,
    sessions: async_sessionmaker[AsyncSession],
    sender: WebhookSender,
) -> FastStream:
    register_handlers(broker, gateway=gateway, clock=clock, sessions=sessions, sender=sender)

    app = FastStream(broker)
    relay: list[asyncio.Task[None]] = []

    @app.on_startup
    async def declare() -> None:
        # до старта подписчиков: иначе первый же отказ на холодном старте
        # публикуется в ещё не объявленную retry-очередь и уходит в DLQ
        await broker.connect()
        await declare_topology(broker)

    @app.after_startup
    async def start_relay() -> None:
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
    # один клиент на процесс: соединения к получателю переиспользуются,
    # а не переустанавливаются на каждую из трёх попыток
    client = make_client()
    app = build_app(
        make_broker(),
        gateway=EmulatedGateway(clock, settings.gateway_force_outcome),
        clock=clock,
        sessions=session_factory(),
        sender=HttpWebhookSender(client),
    )

    @app.on_shutdown
    async def close_resources() -> None:
        await client.aclose()
        await dispose()

    return app


def _event_id(event: dict[str, Any]) -> UUID:
    raw = event.get("event_id")
    if not isinstance(raw, str):
        raise PermanentError("в событии нет event_id")
    try:
        return UUID(raw)
    except ValueError as error:
        raise PermanentError(f"event_id не разбирается: {raw}") from error
