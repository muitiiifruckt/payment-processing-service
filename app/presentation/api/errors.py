import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

CODES = {
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_409_CONFLICT: "idempotency_key_conflict",
    422: "validation_error",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "internal_error",
}

log = logging.getLogger(__name__)


def error_response(status_code: int, message: str) -> JSONResponse:
    code = CODES.get(status_code, "error")
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def install(app: FastAPI) -> None:
    """Единый формат ошибки: смесь дефолта FastAPI и своего читается как недоделка."""

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"][1:]) or "body"
        return error_response(422, f"{where}: {first['msg']}")

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        # текст исключения наружу не отдаём: там оказываются строки подключения
        # и содержимое запроса
        log.exception("необработанная ошибка на %s %s", request.method, request.url.path)
        return error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "внутренняя ошибка сервиса")
