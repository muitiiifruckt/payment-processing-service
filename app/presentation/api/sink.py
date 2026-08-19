from collections import OrderedDict, deque
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status

from app.presentation.api.deps import require_api_key

#: Демонстрационный приёмник из RFC §11: включается флагом, живёт в памяти
#: и в публичную схему не попадает. Отдельным сервисом compose его делать
#: нельзя — состав окружения задан заданием.
MAX_KEPT = 100
MAX_BODY_BYTES = 64 * 1024

router = APIRouter(prefix="/__sink__", include_in_schema=False)
received: deque[dict[str, Any]] = deque(maxlen=MAX_KEPT)
#: Сколько раз обращались по каждому платежу — по этому счётчику видно,
#: что повторы webhook действительно случились. Ограничен так же, как
#: received: приёмник держит всё в памяти процесса
attempts: OrderedDict[str, int] = OrderedDict()


def _keep(payload: dict[str, Any], request: Request) -> bool:
    """Размер тела ограничен: приёмник держит его в памяти процесса."""
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        return False
    received.append(payload)
    return True


def _count(payload: dict[str, Any]) -> int:
    key = str(payload.get("payment_id") or payload.get("event_id") or "")
    attempts[key] = attempts.get(key, 0) + 1
    attempts.move_to_end(key)
    while len(attempts) > MAX_KEPT:
        attempts.popitem(last=False)
    return attempts[key]


@router.post("/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def accept(payload: dict[str, Any], request: Request) -> None:
    # без ключа: настоящий получатель webhook не знает нашего API_KEY
    _keep(payload, request)


@router.post("/flaky/{failures}", status_code=status.HTTP_204_NO_CONTENT)
async def accept_after_failures(
    failures: int, payload: dict[str, Any], request: Request, response: Response
) -> None:
    """Отказывает заданное число раз на каждый платёж, потом принимает.
    Нужен, чтобы сквозной тест видел повторы webhook, а не только успех."""
    if _count(payload) <= failures:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return
    _keep(payload, request)


@router.get("/flaky", dependencies=[Depends(require_api_key)])
async def attempt_counts() -> dict[str, int]:
    return dict(attempts)


@router.get("/webhook", dependencies=[Depends(require_api_key)])
async def listing() -> list[dict[str, Any]]:
    """Под ключом: наружу это содержимое чужих платежей."""
    return list(received)
