from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.application.create_payment import create_payment
from app.application.idempotency import request_hash
from app.domain.money import Currency, Money
from app.domain.payment import Payment
from app.infrastructure.db.payment_repository import PaymentRepository
from app.presentation.api.deps import ClockDep, IdsDep, SessionDep, require_api_key
from app.presentation.api.schemas import (
    ErrorResponse,
    PaymentAccepted,
    PaymentCreate,
    PaymentView,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["payments"],
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)


@router.post(
    "/payments",
    status_code=status.HTTP_202_ACCEPTED,
    responses={409: {"model": ErrorResponse}},
)
async def create(
    body: PaymentCreate,
    session: SessionDep,
    clock: ClockDep,
    ids: IdsDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> PaymentAccepted:
    now = clock.now()
    payment = Payment(
        payment_id=ids.new_id(),
        amount=Money(body.amount, Currency(body.currency)),
        created_at=now,
        idempotency_key=idempotency_key,
        # python-режим, а не json: в json Decimal уже строка, и «100» с «100.00»
        # не свести к одному виду
        request_hash=request_hash(body.model_dump()),
        description=body.description,
        payment_metadata=body.metadata,
        webhook_url=body.webhook_url,
    )

    stored = await create_payment(session, payment, event_id=ids.new_id(), now=now)
    await session.commit()

    if stored.payment_id != payment.payment_id:
        if stored.request_hash != payment.request_hash:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Idempotency-Key уже использован с другим телом запроса",
            )
        # повтор отвечает тем же, чем ответил оригинал: клиент, разбирающий
        # 202 по контракту, не должен ломаться именно на ретрае.
        # Актуальное состояние платежа отдаёт GET
        payment = stored

    return PaymentAccepted(
        payment_id=payment.payment_id, status=payment.status, created_at=payment.created_at
    )


@router.get("/payments/{payment_id}", response_model=PaymentView)
async def get(payment_id: UUID, session: SessionDep) -> PaymentView:
    payment = await PaymentRepository(session).get(payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"платёж {payment_id} не найден")
    return PaymentView.of(payment)
