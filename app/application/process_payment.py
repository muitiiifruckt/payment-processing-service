import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.notify import (
    WebhookNotDeliveredError,
    WebhookSender,
    deliver_webhook,
)
from app.application.policy import CLAIM_LEASE, WEBHOOK_FIRST_PASS_ATTEMPTS
from app.application.ports import Clock, GatewayUnavailableError, PaymentGateway
from app.domain.payment import TERMINAL_STATUSES, Payment
from app.domain.status import PaymentStatus
from app.infrastructure.db.payment_repository import PaymentRepository

log = logging.getLogger(__name__)


class TransientError(Exception):
    """Прогон не удался, но платёж ещё можно обработать — сообщение в повтор."""


class PermanentError(Exception):
    """Повторять бессмысленно — сообщение в DLQ."""


async def process_payment(
    sessions: async_sessionmaker[AsyncSession],
    payment_id: UUID,
    *,
    gateway: PaymentGateway,
    clock: Clock,
    sender: WebhookSender,
    event_id: UUID,
) -> None:
    now = clock.now()
    payment = await _take(sessions, payment_id, now=now)
    if payment.status in TERMINAL_STATUSES:
        # платёж уже обработан, но получатель о нём не знает: одна попытка,
        # брокерный повтор сам по себе является отложенной второй (RFC §6.2)
        await _notify(sessions, payment, event_id=event_id, sender=sender, clock=clock, attempts=1)
        return

    try:
        # вне транзакции: эмуляция длится 2–5 секунд, всё это время
        # соединение с БД должно быть свободно
        succeeded = await gateway.process(payment_id)
    except GatewayUnavailableError as error:
        await _release_quietly(sessions, payment_id, claimed_at=now)
        raise TransientError(str(error)) from error
    except Exception as error:
        await _release_quietly(sessions, payment_id, claimed_at=now)
        raise TransientError(f"шлюз: {type(error).__name__}: {error}") from error

    if succeeded:
        payment.mark_succeeded(clock.now())
    else:
        payment.mark_failed(clock.now())

    async with sessions() as session, session.begin():
        if not await PaymentRepository(session).save_result(payment, claimed_at=now):
            # пока мы ходили в шлюз, результат записал другой прогон.
            # Исход детерминирован по payment_id, так что расхождения нет
            log.info("результат по платежу %s уже записан", payment_id)
            return

    await _notify(
        sessions,
        payment,
        event_id=event_id,
        sender=sender,
        clock=clock,
        attempts=WEBHOOK_FIRST_PASS_ATTEMPTS,
    )


async def _take(
    sessions: async_sessionmaker[AsyncSession], payment_id: UUID, *, now: datetime
) -> Payment:
    """Захват в отдельной короткой транзакции. Терминальный платёж
    возвращается без захвата: шлюз ему уже не нужен, webhook — возможно."""
    async with sessions() as session, session.begin():
        repository = PaymentRepository(session)
        payment = await repository.get(payment_id)
        if payment is None:
            raise PermanentError(f"платёж {payment_id} не найден")
        if payment.status is PaymentStatus.PENDING and not await repository.claim(
            payment_id, now=now, lease=CLAIM_LEASE
        ):
            raise TransientError(f"платёж {payment_id} занят другим обработчиком")
        return payment


async def _release_quietly(
    sessions: async_sessionmaker[AsyncSession], payment_id: UUID, *, claimed_at: datetime
) -> None:
    """Снять захват, не дожидаясь протухания: иначе повтор через 2 секунды
    упрётся в собственную же метку и уйдёт в следующий.

    Своя ошибка проглатывается: БД и шлюз отваливаются вместе, а подменять
    ею причину отказа нельзя — захват в худшем случае протухнет сам."""
    try:
        async with sessions() as session, session.begin():
            await PaymentRepository(session).release(payment_id, claimed_at=claimed_at)
    except Exception:
        log.warning("не удалось снять захват платежа %s", payment_id, exc_info=True)


async def _notify(
    sessions: async_sessionmaker[AsyncSession],
    payment: Payment,
    *,
    event_id: UUID,
    sender: WebhookSender,
    clock: Clock,
    attempts: int,
) -> None:
    """Отметка о доставке ставится только после подтверждения получателем:
    без неё повтор ушёл бы второй раз, с ней раньше времени — не ушёл бы вовсе."""
    if not payment.needs_webhook:
        return
    try:
        delivered = await deliver_webhook(
            payment, event_id=event_id, sender=sender, clock=clock, attempts=attempts
        )
    except WebhookNotDeliveredError as error:
        # RFC §6.2: исчерпание попыток — временный отказ обработки,
        # сообщение уходит в повтор и добьёт webhook на следующем прогоне
        raise TransientError(str(error)) from error
    if not delivered:
        # получатель отверг тело: повторять нечего, платёж обработан
        log.warning("получатель отверг webhook по платежу %s", payment.payment_id)
        return
    async with sessions() as session, session.begin():
        await PaymentRepository(session).mark_webhook_delivered(payment.payment_id, now=clock.now())
