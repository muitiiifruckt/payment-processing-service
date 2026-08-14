from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.payment import Payment


class PaymentRepository:
    """Отображение между доменным платежом и строкой таблицы.

    Домен и таблица — разные классы: ядро не должно знать про SQLAlchemy.
    Цена — явное отображение здесь, зато `Payment` остаётся проверяемым
    за миллисекунды и без БД.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, payment: Payment) -> None: ...

    async def get(self, payment_id: UUID) -> Payment | None:
        return None
