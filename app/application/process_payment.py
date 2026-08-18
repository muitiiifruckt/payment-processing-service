import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.policy import CLAIM_LEASE
from app.application.ports import Clock, GatewayUnavailableError, PaymentGateway
from app.domain.payment import Payment
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
) -> None:
    now = clock.now()
    payment = await _take(sessions, payment_id, now=now)
    if payment is None:
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


async def _take(
    sessions: async_sessionmaker[AsyncSession], payment_id: UUID, *, now: datetime
) -> Payment | None:
    """Захват в отдельной короткой транзакции. None — обрабатывать нечего."""
    async with sessions() as session, session.begin():
        repository = PaymentRepository(session)
        payment = await repository.get(payment_id)
        if payment is None:
            raise PermanentError(f"платёж {payment_id} не найден")
        if payment.status is not PaymentStatus.PENDING:
            return None
        if not await repository.claim(payment_id, now=now, lease=CLAIM_LEASE):
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
