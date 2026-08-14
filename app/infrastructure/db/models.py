"""Таблицы payments и outbox.

Схема целиком задана RFC 0001 — это зафиксированное решение, а не поведение,
которое выводится тестами. Доменная модель растёт отдельно и по мере надобности.

Тип статуса — строка с ограничением, а не нативное перечисление PostgreSQL:
изменение набора значений у нативного типа требует ручных операций в миграциях
и не подхватывается автогенерацией (RFC §13).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


CURRENCIES = ("RUB", "USD", "EUR")
STATUSES = ("pending", "succeeded", "failed")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({allowed})"


class PaymentRow(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        CheckConstraint(_in_list("currency", CURRENCIES), name="ck_payments_currency"),
        CheckConstraint(_in_list("status", STATUSES), name="ck_payments_status"),
        # processed_at заполнен тогда и только тогда, когда статус терминален (RFC §3)
        CheckConstraint(
            "(status = 'pending') = (processed_at IS NULL)",
            name="ck_payments_processed_at_matches_status",
        ),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    # Разрядность 20 и масштаб 4: запас под валюты с тремя знаками и будущие
    # комиссии, наружу отдаётся всегда 2 знака (RFC §8.1)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    currency: Mapped[str] = mapped_column(String(3))

    description: Mapped[str | None] = mapped_column(String(512))
    # metadata занято DeclarativeBase.metadata, поэтому атрибут переименован,
    # а имя колонки остаётся тем, что в задании
    payment_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=text("'{}'::jsonb")
    )

    status: Mapped[str] = mapped_column(String(16), index=True)

    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))

    webhook_url: Mapped[str | None] = mapped_column(Text)
    webhook_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Захват в работу — метка времени, а не четвёртый статус (RFC §3)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class OutboxRow(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        # Выборка идёт только по неопубликованным, поэтому индекс частичный:
        # рост таблицы не замедляет relay (RFC §5.3)
        Index(
            "ix_outbox_unpublished",
            "next_attempt_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    #: Он же event_id: стабилен и переиспользуется при всех повторных
    #: публикациях и доставках (RFC §9)
    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # Без времени следующей попытки одно неопубликуемое событие даёт
    # бесконечный горячий цикл (RFC §5.3)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
