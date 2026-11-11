"""Shared fixtures. Database tests run against a throwaway database, never the app's own."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import REPO_ROOT, get_settings

TEST_DB = "tradehub_test"
ALEMBIC_INI = REPO_ROOT / "db" / "alembic.ini"


@pytest.fixture(scope="session")
def seeds_dir() -> Path:
    return REPO_ROOT / "db" / "seeds"


def alembic_config(database_url) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["database_url"] = database_url
    return config


@pytest.fixture(scope="session")
def empty_db_url():
    """A freshly created, empty database. Fails loudly if PostgreSQL is unreachable."""
    settings = get_settings()
    admin = create_engine(settings.database_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
    yield settings.database_url(TEST_DB)
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
    admin.dispose()


@pytest.fixture(scope="session")
def migrated_engine(empty_db_url):
    command.upgrade(alembic_config(empty_db_url), "head")
    engine = create_engine(empty_db_url)
    yield engine
    engine.dispose()
