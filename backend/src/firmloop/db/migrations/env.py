"""Alembic environment: runs migrations through the async engine."""

import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection

from firmloop.db.engine import create_db_engine
from firmloop.db.models import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    # The driver is in autocommit mode, so wrap the whole run in one explicit transaction: SQLite
    # DDL is transactional, and a failure leaves neither tables nor a version stamp behind.
    connection.exec_driver_sql("BEGIN")
    try:
        with context.begin_transaction():
            context.run_migrations()
    except BaseException:
        connection.rollback()
        raise
    connection.commit()


async def _run_async() -> None:
    engine = create_db_engine(_database_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async())


run_migrations_online()
