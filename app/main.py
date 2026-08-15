from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from sqlalchemy import text

from app.infrastructure.db.session import dispose
from app.presentation.api import errors
from app.presentation.api.deps import SessionDep
from app.presentation.api.routes import router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Payment Processing Service",
        version="1.0.0",
        lifespan=lifespan,
    )
    errors.install(app)
    app.include_router(router)

    # Без X-API-Key: иначе ни compose, ни CI не смогут дождаться готовности
    @app.get("/health", include_in_schema=False)
    async def health(session: SessionDep) -> dict[str, str]:
        # проба обязана трогать базу: иначе compose объявляет сервис готовым
        # раньше, чем он способен принять первый платёж
        try:
            await session.execute(text("SELECT 1"))
        except Exception:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "база данных недоступна"
            ) from None
        return {"status": "ok", "database": "ok"}

    return app


app = create_app()
