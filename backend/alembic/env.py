"""Alembic environment for raw SQL migrations (no ORM models).

Import paths: this file runs from ``backend/alembic/``, so we add ``backend/``
(not ``backend/alembic/``) to ``sys.path`` so ``app.core.config`` resolves.
The DB URL comes from :func:`app.core.config.get_app_database_url`, the same
function the rest of the application uses (single source of truth, no duplicate
env parsing).
"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add backend/ (parent of alembic/) to sys.path so app.* imports resolve.
_backend_dir = os.path.dirname(os.path.dirname(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from app.core.config import get_app_database_url  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Get database URL from the single source of truth (same as the app).
# Escape '%' for configparser interpolation (known Alembic gotcha with
# URL-encoded passwords containing special characters like '@', '!', etc.).
database_url = get_app_database_url()
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be present.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
