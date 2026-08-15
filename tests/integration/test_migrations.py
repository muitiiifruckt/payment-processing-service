import asyncio
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parent.parent.parent
SCRATCH_DB = "migrations_roundtrip"


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def test_migrations_apply_and_roll_back(database_url: str) -> None:
    """На отдельной базе: откат на общей снёс бы схему у остальных тестов."""
    admin = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
        await connection.execute(sa.text(f'CREATE DATABASE "{SCRATCH_DB}"'))
    await admin.dispose()

    scratch_url = database_url.rsplit("/", 1)[0] + f"/{SCRATCH_DB}"
    config = alembic_config(scratch_url)

    try:
        # alembic внутри зовёт asyncio.run — из работающего цикла это нельзя
        await asyncio.to_thread(command.upgrade, config, "head")
        engine = create_async_engine(scratch_url)
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: sa.inspect(sync).get_table_names())
        await engine.dispose()
        assert {"payments", "outbox"} <= set(tables)

        await asyncio.to_thread(command.downgrade, config, "base")
        engine = create_async_engine(scratch_url)
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: sa.inspect(sync).get_table_names())
        await engine.dispose()
        assert {"payments", "outbox"} & set(tables) == set()
    finally:
        admin = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
        async with admin.connect() as connection:
            await connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))
        await admin.dispose()
