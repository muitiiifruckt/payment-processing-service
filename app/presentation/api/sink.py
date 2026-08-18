from collections import deque
from typing import Any

from fastapi import APIRouter, Depends, Request, status

from app.presentation.api.deps import require_api_key

#: Демонстрационный приёмник из RFC §11: включается флагом, живёт в памяти
#: и в публичную схему не попадает. Отдельным сервисом compose его делать
#: нельзя — состав окружения задан заданием.
MAX_KEPT = 100
MAX_BODY_BYTES = 64 * 1024

router = APIRouter(prefix="/__sink__", include_in_schema=False)
received: deque[dict[str, Any]] = deque(maxlen=MAX_KEPT)


@router.post("/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def accept(payload: dict[str, Any], request: Request) -> None:
    # без ключа: настоящий получатель webhook не знает нашего API_KEY.
    # Зато размер тела ограничен — приёмник держит его в памяти процесса
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        return
    received.append(payload)


@router.get("/webhook", dependencies=[Depends(require_api_key)])
async def listing() -> list[dict[str, Any]]:
    """Под ключом: наружу это содержимое чужих платежей."""
    return list(received)
