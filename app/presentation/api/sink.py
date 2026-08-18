from collections import deque
from typing import Any

from fastapi import APIRouter, status

#: Демонстрационный приёмник из RFC §11: включается флагом, живёт в памяти
#: и в публичную схему не попадает. Отдельным сервисом compose его делать
#: нельзя — состав окружения задан заданием.
MAX_KEPT = 100

router = APIRouter(prefix="/__sink__", include_in_schema=False)
received: deque[dict[str, Any]] = deque(maxlen=MAX_KEPT)


@router.post("/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def accept(payload: dict[str, Any]) -> None:
    received.append(payload)


@router.get("/webhook")
async def listing() -> list[dict[str, Any]]:
    return list(received)
