"""Alembic migration environment."""

import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from switchboard.adapters.persistence.schema import metadata
from switchboard.bootstrap.config import load_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def resolve_database_url() -> str:
    """Resolve a programmatic URL or fall back to runtime settings."""

    configured_url = config.get_main_option("sqlalchemy.url")

    if configured_url.startswith("postgresql+psycopg://"):
        return configured_url

    return load_settings().database_url


database_url = resolve_database_url()

# Alembic Config uses ConfigParser. Percent signs in encoded credentials
# must be escaped before being stored as an option.
config.set_main_option(
    "sqlalchemy.url",
    database_url.replace("%", "%%"),
)

target_metadata = metadata


def run_migrations_offline() -> None:
    """Run migrations without opening a database connection."""

    database_url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with a synchronous connection facade."""

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Construct an async engine and run Alembic migrations."""

    configuration = config.get_section(config.config_ini_section) or {}

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations using a live database connection."""

    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(run_async_migrations(), loop_factory=loop_factory)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
