import hashlib
from uuid import UUID

from app.application.ports import Clock

SUCCESS_RATE = 0.9
MIN_DURATION = 2.0
MAX_DURATION = 5.0

#: Два независимых числа из одного идентификатора: иначе длительность
#: и исход окажутся связаны, и все медленные платежи станут неуспешными
_OUTCOME_SALT = b"outcome"
_DURATION_SALT = b"duration"


def _unit(payment_id: UUID, salt: bytes) -> float:
    """Равномерное [0, 1). blake2b, а не hash(): встроенный солится на запуск."""
    digest = hashlib.blake2b(payment_id.bytes + salt, digest_size=8).digest()
    return int.from_bytes(digest) / 2**64


def outcome_for(payment_id: UUID) -> bool:
    return _unit(payment_id, _OUTCOME_SALT) < SUCCESS_RATE


def duration_for(payment_id: UUID) -> float:
    return MIN_DURATION + _unit(payment_id, _DURATION_SALT) * (MAX_DURATION - MIN_DURATION)


class EmulatedGateway:
    """Исход и длительность выведены из payment_id: повторная обработка
    того же платежа обязана давать тот же ответ."""

    def __init__(self, clock: Clock, force_outcome: str | None = None) -> None:
        self._clock = clock
        self._force_outcome = force_outcome

    async def process(self, payment_id: UUID) -> bool:
        await self._clock.sleep(duration_for(payment_id))
        if self._force_outcome is not None:
            return self._force_outcome == "succeeded"
        return outcome_for(payment_id)
