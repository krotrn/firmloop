"""Run Alembic migrations programmatically (startup and tests)."""

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_to_head(database_url: str) -> None:
    """Apply all migrations. Blocking, with its own event loop: call via `asyncio.to_thread`."""
    command.upgrade(alembic_config(database_url), "head")


if __name__ == "__main__":
    import logging

    from firmloop.config import Settings

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    upgrade_to_head(Settings().database_url)
    logging.getLogger(__name__).info("database migrated to the latest revision")
