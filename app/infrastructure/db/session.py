from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.infrastructure.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        # пул делят обработчики и relay внутри одного процесса
        _engine = create_async_engine(settings.database_url, pool_size=15, pool_pre_ping=True)
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        # без expire_on_commit=False обращение к атрибуту после коммита
        # уходит в ленивую подгрузку и падает вне greenlet
        _sessionmaker = async_sessionmaker(bind=engine(), expire_on_commit=False)
    return _sessionmaker


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
