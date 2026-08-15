import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.clock import SystemClock, Uuid4Generator
from app.infrastructure.config import settings
from app.infrastructure.db.session import session_factory

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: Annotated[str | None, Security(api_key_header)]) -> None:
    if key is None or not secrets.compare_digest(key, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "неверный или отсутствующий X-API-Key")


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
ClockDep = Annotated[SystemClock, Depends(SystemClock)]
IdsDep = Annotated[Uuid4Generator, Depends(Uuid4Generator)]
