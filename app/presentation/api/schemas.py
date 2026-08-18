import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.money import MAX_AMOUNT, Currency
from app.domain.payment import Payment
from app.domain.status import PaymentStatus
from app.infrastructure.config import settings


class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # верхняя граница та же, что в домене: иначе InvalidAmountError вылетает
    # мимо валидации и превращается в 500
    amount: Decimal = Field(gt=0, lt=MAX_AMOUNT)
    currency: Currency
    description: str | None = Field(default=None, max_length=settings.description_max_length)
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str | None = Field(default=None, max_length=2048)

    @field_validator("amount")
    @classmethod
    def at_most_two_decimals(cls, value: Decimal) -> Decimal:
        exponent = value.as_tuple().exponent
        if not isinstance(exponent, int) or -exponent > 2:
            raise ValueError("не более двух знаков после запятой")
        return value

    @field_validator("webhook_url")
    @classmethod
    def http_scheme_only(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("webhook_url должен начинаться с http:// или https://")
        return value

    @model_validator(mode="after")
    def metadata_within_limit(self) -> Self:
        encoded = json.dumps(self.metadata, ensure_ascii=False).encode()
        if len(encoded) > settings.metadata_max_bytes:
            raise ValueError(f"метаданные больше {settings.metadata_max_bytes} байт")
        return self


class PaymentAccepted(BaseModel):
    payment_id: UUID
    status: PaymentStatus
    created_at: datetime


class PaymentView(BaseModel):
    payment_id: UUID
    amount: str
    currency: Currency
    description: str | None
    metadata: dict[str, Any]
    status: PaymentStatus
    webhook_url: str | None
    created_at: datetime
    processed_at: datetime | None

    @classmethod
    def of(cls, payment: Payment) -> "PaymentView":
        return cls(
            payment_id=payment.payment_id,
            amount=payment.amount.formatted,
            currency=payment.amount.currency,
            description=payment.description,
            metadata=payment.payment_metadata,
            status=payment.status,
            webhook_url=payment.webhook_url,
            created_at=payment.created_at,
            processed_at=payment.processed_at,
        )


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
