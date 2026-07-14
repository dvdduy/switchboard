"""Integration tests for the complete migration chain."""

from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

EXPECTED_DOMAIN_TABLES = {
    "agent_tool_bindings",
    "agent_definitions",
    "agent_versions",
    "command_receipts",
    "conversations",
    "conversation_summaries",
    "messages",
    "turns",
    "turn_attempts",
    "execution_events",
    "tool_conformance_case_results",
    "tool_conformance_runs",
    "tool_definitions",
    "tool_version_states",
    "tool_versions",
}


def get_table_names(database_url: str) -> set[str]:
    engine = create_engine(database_url)

    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_context_migration_backfills_existing_agent_versions(
    alembic_config: Config,
    test_database_url: str,
) -> None:
    definition_id = uuid4()
    version_id = uuid4()
    command.downgrade(alembic_config, "443feebc380e")
    engine = create_engine(test_database_url)

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO agent_definitions (id, team_id, name, created_at)
                    VALUES (:id, :team_id, 'Historical agent', now())
                    """
                ),
                {"id": definition_id, "team_id": uuid4()},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO agent_versions
                        (id, agent_definition_id, version_number, created_at)
                    VALUES (:id, :definition_id, 1, now())
                    """
                ),
                {"id": version_id, "definition_id": definition_id},
            )

        command.upgrade(alembic_config, "head")

        with engine.connect() as connection:
            policy = connection.execute(
                text(
                    """
                    SELECT model_window_tokens, reserved_output_tokens,
                           fixed_overhead_tokens, summary_max_tokens,
                           minimum_recent_messages
                    FROM agent_versions WHERE id = :id
                    """
                ),
                {"id": version_id},
            ).one()

        assert tuple(policy) == (4096, 512, 256, 256, 1)
        columns = inspect(engine).get_columns("agent_versions")
        policy_names = {
            "model_window_tokens",
            "reserved_output_tokens",
            "fixed_overhead_tokens",
            "summary_max_tokens",
            "minimum_recent_messages",
        }
        assert all(
            column["default"] is None for column in columns if column["name"] in policy_names
        )
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM agent_versions WHERE id = :id"),
                {"id": version_id},
            )
            connection.execute(
                text("DELETE FROM agent_definitions WHERE id = :id"),
                {"id": definition_id},
            )
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
