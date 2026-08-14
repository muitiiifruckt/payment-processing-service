"""Общие фикстуры.

Разделение по стоимости запуска задаётся каталогом: всё в tests/integration
и tests/e2e помечается автоматически, чтобы не расставлять маркеры руками
и не забыть их в новом файле.

Для БД поднимается настоящий контейнер — один на сессию. Схема накатывается
Alembic'ом, так что каждый прогон заодно проверяет миграции.
"""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

ROOT = Path(__file__).resolve().parent.parent


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    for item in items:
        path = str(item.path).replace("\\", "/")
        if "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)
        elif "/tests/e2e/" in path:
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Адрес готового экземпляра, если задан снаружи; иначе свой контейнер.

    В CI адрес задан сервис-контейнером — те же тесты работают без изменений.
    """
    external = os.getenv("TEST_DATABASE_URL")
    if external:
        yield external
        return

    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def migrated_database(database_url: str) -> str:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


@pytest.fixture(scope="session")
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Сессия внутри внешней транзакции, которая откатывается после теста.

    Изоляция без пересоздания схемы. Тесты, которым нужен настоящий коммит —
    в первую очередь проверка атомарности outbox, — берут session_factory.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        maker = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with maker() as session:
            yield session
        await transaction.rollback()


@pytest.fixture
async def session_factory(
    engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Настоящие коммиты. Чистка — за самим тестом."""
    yield async_sessionmaker(bind=engine, expire_on_commit=False)
