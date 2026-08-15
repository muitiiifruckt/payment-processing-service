from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.infrastructure.db.session import dispose
from app.presentation.api import errors
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
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
