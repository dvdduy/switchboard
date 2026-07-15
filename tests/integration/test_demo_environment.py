import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.bootstrap.config import Settings
from switchboard.bootstrap.demo_environment import (
    DEMO_AGENT_DEFINITION_ID,
    DEMO_AGENT_VERSION_ID,
    DEMO_CONTEXT_POLICY,
    DEMO_TEAM_ID,
    SEEDED_AT,
    DemoSeedConflictError,
    inspect_demo_seed,
    reset_demo_environment,
    seed_demo_environment,
    validate_demo_environment,
)
from switchboard.domain.agents import AgentDefinition
from switchboard.domain.tools import ToolConformanceStatus, ToolLifecycleStatus


async def test_seed_is_repeatable_and_produces_active_bound_conformant_tools(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    first = await seed_demo_environment(unit_of_work_factory)
    second = await seed_demo_environment(unit_of_work_factory)

    assert first == second
    assert first.team_id == DEMO_TEAM_ID
    assert first.agent_version_id == DEMO_AGENT_VERSION_ID
    assert first.tool_keys == ("search_work_items", "update_due_date")
    assert first.reference_work_item_ids == ("WI-1", "WI-2", "WI-3")

    status = await inspect_demo_seed(unit_of_work_factory)
    assert status.ready
    assert status.issues == ()

    async with unit_of_work_factory() as unit_of_work:
        agent_version = await unit_of_work.agents.get_version(DEMO_AGENT_VERSION_ID)
        assert agent_version is not None
        assert agent_version.version_number == 3
        assert agent_version.context_policy == DEMO_CONTEXT_POLICY

        eligible = await unit_of_work.tools.list_eligible_for_agent(
            team_id=DEMO_TEAM_ID,
            agent_version_id=DEMO_AGENT_VERSION_ID,
        )
        assert [tool.definition.tool_key for tool in eligible] == [
            "search_work_items",
            "update_due_date",
        ]
        for tool in eligible:
            state = await unit_of_work.tools.get_version_state(tool.version.id)
            assert state is not None
            assert state.status is ToolLifecycleStatus.ACTIVE
            assert state.activated_conformance_run_id is not None
            stored = await unit_of_work.tools.get_conformance_run(
                state.activated_conformance_run_id
            )
            assert stored is not None
            run, cases = stored
            assert run.status is ToolConformanceStatus.PASSED
            assert len(cases) == 8
            assert all(case.status is ToolConformanceStatus.PASSED for case in cases)


async def test_partial_seed_requires_explicit_reset(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.agents.add_definition(
            AgentDefinition(
                id=DEMO_AGENT_DEFINITION_ID,
                team_id=DEMO_TEAM_ID,
                name="Switchboard Phase 1 Demo",
                created_at=SEEDED_AT,
            )
        )
        await unit_of_work.commit()

    with pytest.raises(DemoSeedConflictError, match="run the guarded reset"):
        await seed_demo_environment(unit_of_work_factory)


async def test_validation_reports_migration_and_reset_clears_seed(
    database_engine: AsyncEngine,
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    test_database_url: str,
) -> None:
    initial = await validate_demo_environment(database_engine, unit_of_work_factory)
    assert initial.migration_revision == "g4d5e6f7a8b9"
    assert not initial.seed.ready

    await seed_demo_environment(unit_of_work_factory)
    seeded = await validate_demo_environment(database_engine, unit_of_work_factory)
    assert seeded.seed.ready

    settings = Settings.model_validate(
        {
            "environment": "test",
            "database_url": test_database_url,
            "redis_url": "redis://localhost:6379/15",
        }
    )
    await reset_demo_environment(database_engine, settings)

    reset = await inspect_demo_seed(unit_of_work_factory)
    assert not reset.ready
    assert reset.present_record_count == 0
