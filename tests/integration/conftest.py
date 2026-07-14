"""Shared PostgreSQL integration-test fixtures."""

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from pathlib import Path

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

from switchboard.adapters.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://switchboard:switchboard@localhost:5433/switchboard_test"
)

TRUNCATE_DOMAIN_TABLES = text(
    """
    TRUNCATE TABLE
        agent_tool_bindings,
        tool_version_states,
        tool_conformance_case_results,
        tool_conformance_runs,
        tool_versions,
        tool_definitions,
        conversation_summaries,
        execution_events,
        turn_attempts,
        turns,
        messages,
        conversations,
        agent_versions,
        agent_definitions
    CASCADE
    """
)


def pytest_asyncio_loop_factories(
    config: pytest.Config,
    item: pytest.Item,
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    """Provide an async-Psycopg-compatible loop factory on Windows."""

    del config, item

    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}

    return {"default": asyncio.new_event_loop}


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Return the isolated PostgreSQL integration-test URL."""

    return os.getenv(
        "SWITCHBOARD_TEST_DATABASE_URL",
        DEFAULT_TEST_DATABASE_URL,
    )


@pytest.fixture(scope="session")
def alembic_config(test_database_url: str) -> Config:
    """Create Alembic configuration targeting the test database."""

    configuration = Config(str(PROJECT_ROOT / "alembic.ini"))

    configuration.set_main_option(
        "script_location",
        str(PROJECT_ROOT / "migrations"),
    )
    configuration.set_main_option(
        "sqlalchemy.url",
        test_database_url.replace("%", "%%"),
    )

    return configuration


@pytest.fixture(scope="session", autouse=True)
def migrated_database(
    alembic_config: Config,
) -> Iterator[None]:
    """Ensure the test database starts at the latest migration."""

    command.upgrade(alembic_config, "head")
    yield


async def truncate_domain_tables(
    engine: AsyncEngine,
) -> None:
    """Remove all domain data while preserving the schema."""

    async with engine.begin() as connection:
        await connection.execute(TRUNCATE_DOMAIN_TABLES)


@pytest.fixture
async def database_engine(
    test_database_url: str,
    migrated_database: None,
) -> AsyncIterator[AsyncEngine]:
    """Provide a clean async engine for one integration test."""

    del migrated_database

    engine = create_async_engine(
        test_database_url,
        pool_pre_ping=True,
    )

    await truncate_domain_tables(engine)

    try:
        yield engine
    finally:
        await truncate_domain_tables(engine)
        await engine.dispose()


@pytest.fixture
async def unit_of_work_factory(
    database_engine: AsyncEngine,
) -> SqlAlchemyUnitOfWorkFactory:
    """Create real PostgreSQL-backed units of work."""

    session_factory = async_sessionmaker(
        database_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    return SqlAlchemyUnitOfWorkFactory(session_factory)
