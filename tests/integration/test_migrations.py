"""Integration tests for the complete migration chain."""

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

EXPECTED_DOMAIN_TABLES = {
    "agent_definitions",
    "agent_versions",
    "conversations",
    "messages",
    "turns",
    "turn_attempts",
}


def get_table_names(database_url: str) -> set[str]:
    engine = create_engine(database_url)

    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_migrations_upgrade_cleanly_from_base(
    alembic_config: Config,
    test_database_url: str,
) -> None:
    command.downgrade(alembic_config, "base")

    try:
        tables_at_base = get_table_names(test_database_url)

        assert EXPECTED_DOMAIN_TABLES.isdisjoint(tables_at_base)
    finally:
        # Always restore the database for subsequent tests, including when
        # the base-state assertion fails.
        command.upgrade(alembic_config, "head")

    tables_at_head = get_table_names(test_database_url)

    assert tables_at_head >= EXPECTED_DOMAIN_TABLES
    assert "alembic_version" in tables_at_head
