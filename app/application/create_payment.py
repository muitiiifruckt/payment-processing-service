"""Приём платежа.

Транзакционная граница outbox: платёж и событие пишутся одной транзакцией,
одним коммитом (RFC §5.1). Публикация в брокер сюда не входит вовсе — иначе
недоступность брокера роняла бы приём платежа, а весь смысл паттерна в том,
чтобы не роняла.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.payment import Payment

PAYMENT_CREATED = "payment.created"


async def create_payment(
    session: AsyncSession,
    payment: Payment,
    *,
    event_id: UUID,
    now: datetime,
) -> None: ...
