import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

# до импорта app: настройки без дефолта читаются на импорте модуля
os.environ.setdefault("API_KEY", "test-api-key")

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

ROOT = Path(__file__).resolve().parent.parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Маркер по каталогу, чтобы не забыть его в новом файле."""
    for item in items:
        path = str(item.path).replace("\\", "/")
        if "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)
        elif "/tests/e2e/" in path:
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Готовый экземпляр, если задан снаружи (CI), иначе свой контейнер."""
    external = os.getenv("TEST_DATABASE_URL")
    if external:
        yield external
        return

    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def rabbitmq_url() -> Iterator[str]:
    external = os.getenv("TEST_RABBITMQ_URL")
    if external:
        yield external
        return

    from testcontainers.community.rabbitmq import RabbitMqContainer

    with RabbitMqContainer("rabbitmq:4-alpine") as container:
        yield (
            f"amqp://{container.username}:{container.password}"
            f"@{container.get_container_host_ip()}:{container.get_exposed_port(5672)}/"
        )


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    # через attributes, а не set_main_option: ini интерполирует % в пароле
    config.attributes["database_url"] = url
    return config


@pytest.fixture(scope="session")
def migrated_database(database_url: str) -> str:
    command.upgrade(alembic_config(database_url), "head")
    return database_url


@pytest.fixture(scope="session")
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Внешняя транзакция с откатом после теста."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        maker = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with maker() as session:
            yield session
        # тест мог откатиться сам — например, после нарушения уникальности
        if transaction.is_active:
            await transaction.rollback()


@pytest.fixture
async def session_factory(
    engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Настоящие коммиты — для проверки атомарности и блокировок.

    Записанное чистится после теста: иначе оставленные строки outbox попадают
    в чужую выборку, а на переиспользуемой базе копятся между прогонами.
    """
    yield async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE outbox, payments"))
