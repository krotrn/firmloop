"""Async SQLite engine with the connection pragmas applied on every connection."""

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

PRAGMAS: tuple[str, ...] = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
    "PRAGMA synchronous=NORMAL",
)


def _on_connect(dbapi_connection: Any, _record: Any) -> None:
    # Autocommit at the driver level: transactions are started explicitly (BEGIN IMMEDIATE for
    # writes), so the driver never opens a deferred transaction behind our back.
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    try:
        for pragma in PRAGMAS:
            cursor.execute(pragma)
    finally:
        cursor.close()


def create_db_engine(database_url: str) -> AsyncEngine:
    """Create the async engine for `database_url` (e.g. `sqlite+aiosqlite:////data/firmloop.db`)."""
    engine = create_async_engine(database_url)
    event.listen(engine.sync_engine, "connect", _on_connect)
    return engine
