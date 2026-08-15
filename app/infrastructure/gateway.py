from uuid import UUID

from app.application.ports import Clock


class EmulatedGateway:
    """Исход и длительность выведены из payment_id: повторная обработка
    того же платежа обязана давать тот же ответ."""

    def __init__(self, clock: Clock, force_outcome: str | None = None) -> None:
        self._clock = clock
        self._force_outcome = force_outcome

    async def process(self, payment_id: UUID) -> bool:
        return True


def outcome_for(payment_id: UUID) -> bool:
    return True


def duration_for(payment_id: UUID) -> float:
    return 0.0
