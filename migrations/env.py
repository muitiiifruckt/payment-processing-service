import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.infrastructure.config import settings
from app.infrastructure.db.models import Base

config = context.config


# URL не кладём в ini: ConfigParser интерполирует %, и пароль с этим символом
# уронит конфигурацию. attributes — обычный dict, интерполяции там нет
def database_url() -> str:
    injected = config.attributes.get("database_url")
    return str(injected) if injected else settings.database_url


if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # без этого автогенерация не заметит смену Numeric(18,2) на Numeric(20,4)
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(
        database_url(),
        # обязателен: иначе на выходе из asyncio.run в пуле остаются живые
        # соединения и получаем «Event loop is closed»
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
