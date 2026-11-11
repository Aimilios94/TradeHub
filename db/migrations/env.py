"""Alembic environment. Migrations are hand-written SQL; there is no ORM metadata."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Tests pass their own URL with: config.attributes["database_url"] = ...
database_url = config.attributes.get("database_url") or get_settings().database_url()

if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a live database.")

engine = create_engine(database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
