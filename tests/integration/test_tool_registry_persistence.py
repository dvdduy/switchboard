import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.application.errors import ToolVersionLifecycleConflictError
from switchboard.application.ports.tool_adapter import (
    ToolInvocationFailure,
    ToolInvocationRequest,
    ToolInvocationSuccess,
    ToolReconciliationResult,
)
from switchboard.application.services.tool_conformance import (
    ToolConformanceRunner,
    ToolConformanceSuite,
)
from switchboard.application.use_cases.manage_tools import (
    RunToolConformance,
    RunToolConformanceCommand,
)
from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.context import ContextPolicy
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentToolBindingId,
    AgentVersionId,
    TeamId,
    ToolConformanceCaseResultId,
    ToolConformanceRunId,
    ToolDefinitionId,
    ToolVersionId,
)
from switchboard.domain.tools import (
    JSON_SCHEMA_DRAFT_2020_12,
    TOOL_MANIFEST_SCHEMA_VERSION,
    AgentToolBinding,
    IdempotencyMode,
    ReconciliationMode,
    RetryPolicy,
    ToolConformanceCaseResult,
    ToolConformanceRun,
    ToolConformanceStatus,
    ToolDefinition,
    ToolEffect,
    ToolLifecycleStatus,
    ToolManifest,
)

NOW = datetime(2026, 7, 14, 7, 0, tzinfo=UTC)


def manifest() -> ToolManifest:
    schema = {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    return ToolManifest(
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
        display_name="Read value",
        description="Read one deterministic value.",
        input_schema=schema,
        output_schema=schema,
        effect=ToolEffect.READ_ONLY,
        required_scopes=("values:read",),
        timeout_ms=2_000,
        retry_policy=RetryPolicy(2, 100, ("temporarily_unavailable",)),
        idempotency=IdempotencyMode.NONE,
        reconciliation=ReconciliationMode.NONE,
        adapter_key="reference.read_value.v1",
    )


async def persist_definition(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    team_id: TeamId | None = None,
    tool_key: str = "read_value",
) -> ToolDefinition:
    definition = ToolDefinition(
        id=ToolDefinitionId(uuid4()),
        team_id=team_id or TeamId(uuid4()),
        tool_key=tool_key,
        created_at=NOW,
    )
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tools.add_definition(definition)
        await unit_of_work.commit()
    return definition


async def publish(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    definition: ToolDefinition,
) -> ToolVersionId:
    version_id = ToolVersionId(uuid4())
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tools.add_next_version(
            tool_version_id=version_id,
            tool_definition_id=definition.id,
            manifest=manifest(),
            created_at=NOW,
        )
        await unit_of_work.commit()
    return version_id


@pytest.mark.asyncio(loop_scope="module")
async def test_version_and_draft_state_round_trip(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    definition = await persist_definition(unit_of_work_factory)
    version_id = await publish(unit_of_work_factory, definition)

    async with unit_of_work_factory() as unit_of_work:
        stored_definition = await unit_of_work.tools.get_definition_by_key(
            team_id=definition.team_id,
            tool_key=definition.tool_key,
        )
        version = await unit_of_work.tools.get_version(version_id)
        state = await unit_of_work.tools.get_version_state(version_id)

    assert stored_definition == definition
    assert version is not None
    assert version.version_number == 1
    assert version.manifest == manifest()
    assert state is not None
    assert state.status is ToolLifecycleStatus.DRAFT
    assert state.revision == 1


@pytest.mark.asyncio(loop_scope="module")
async def test_concurrent_publication_allocates_distinct_ordered_versions(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    definition = await persist_definition(unit_of_work_factory)

    async def allocate() -> int:
        async with unit_of_work_factory() as unit_of_work:
            version = await unit_of_work.tools.add_next_version(
                tool_version_id=ToolVersionId(uuid4()),
                tool_definition_id=definition.id,
                manifest=manifest(),
                created_at=NOW,
            )
            await unit_of_work.commit()
            return version.version_number

    assert set(await asyncio.gather(allocate(), allocate())) == {1, 2}


async def test_tool_key_is_unique_only_within_its_owning_team(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    await persist_definition(unit_of_work_factory, team_id=team_id, tool_key="shared_key")

    with pytest.raises(IntegrityError):
        await persist_definition(unit_of_work_factory, team_id=team_id, tool_key="shared_key")

    other_team_definition = await persist_definition(
        unit_of_work_factory,
        team_id=TeamId(uuid4()),
        tool_key="shared_key",
    )

    assert other_team_definition.tool_key == "shared_key"


@pytest.mark.asyncio(loop_scope="module")
async def test_rolled_back_publication_leaves_no_version_or_sequence_gap(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    definition = await persist_definition(unit_of_work_factory)
    rolled_back_id = ToolVersionId(uuid4())

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tools.add_next_version(
            tool_version_id=rolled_back_id,
            tool_definition_id=definition.id,
            manifest=manifest(),
            created_at=NOW,
        )

    committed_id = await publish(unit_of_work_factory, definition)
    async with unit_of_work_factory() as unit_of_work:
        assert await unit_of_work.tools.get_version(rolled_back_id) is None
        committed = await unit_of_work.tools.get_version(committed_id)

    assert committed is not None
    assert committed.version_number == 1


@pytest.mark.asyncio(loop_scope="module")
async def test_binding_requires_matching_definition_and_unique_stable_tool(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    first_definition = await persist_definition(
        unit_of_work_factory, team_id=team_id, tool_key="first_tool"
    )
    second_definition = await persist_definition(
        unit_of_work_factory, team_id=team_id, tool_key="second_tool"
    )
    first_version_id = await publish(unit_of_work_factory, first_definition)
    second_version_id = await publish(unit_of_work_factory, second_definition)
    agent_definition = AgentDefinition(
        id=AgentDefinitionId(uuid4()),
        team_id=team_id,
        name="Bound agent",
        created_at=NOW,
    )
    agent_version = AgentVersion(
        id=AgentVersionId(uuid4()),
        agent_definition_id=agent_definition.id,
        version_number=1,
        context_policy=ContextPolicy(4096, 512, 256, 256, 1),
        created_at=NOW,
    )
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.agents.add_definition(agent_definition)
        await unit_of_work.agents.add_version(agent_version)
        await unit_of_work.commit()

    valid = AgentToolBinding(
        id=AgentToolBindingId(uuid4()),
        agent_version_id=agent_version.id,
        tool_definition_id=first_definition.id,
        tool_version_id=first_version_id,
        created_at=NOW,
    )
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tools.add_binding(valid)
        await unit_of_work.commit()

    duplicate = AgentToolBinding(
        id=AgentToolBindingId(uuid4()),
        agent_version_id=agent_version.id,
        tool_definition_id=first_definition.id,
        tool_version_id=first_version_id,
        created_at=NOW,
    )
    with pytest.raises(IntegrityError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.tools.add_binding(duplicate)
            await unit_of_work.commit()

    mismatched = AgentToolBinding(
        id=AgentToolBindingId(uuid4()),
        agent_version_id=agent_version.id,
        tool_definition_id=first_definition.id,
        tool_version_id=second_version_id,
        created_at=NOW,
    )
    with pytest.raises(IntegrityError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.tools.add_binding(mismatched)
            await unit_of_work.commit()


@pytest.mark.asyncio(loop_scope="module")
async def test_conformance_is_atomic_and_lifecycle_update_is_compare_and_set(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    definition = await persist_definition(unit_of_work_factory)
    version_id = await publish(unit_of_work_factory, definition)
    run = ToolConformanceRun(
        id=ToolConformanceRunId(uuid4()),
        tool_version_id=version_id,
        status=ToolConformanceStatus.PASSED,
        started_at=NOW,
        completed_at=NOW + timedelta(milliseconds=5),
    )
    case = ToolConformanceCaseResult(
        id=ToolConformanceCaseResultId(uuid4()),
        run_id=run.id,
        case_key="valid_input",
        status=ToolConformanceStatus.PASSED,
        duration_ms=5,
        diagnostic_code=None,
    )
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tools.add_conformance_run(run, (case,))
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        initial = await unit_of_work.tools.get_version_state(version_id)
    assert initial is not None
    active = initial.activate(conformance_run_id=run.id, at=NOW + timedelta(seconds=1))

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tools.update_version_state(previous=initial, updated=active)
        await unit_of_work.commit()

    stale_update = initial.disable(at=NOW + timedelta(seconds=2))
    with pytest.raises(ToolVersionLifecycleConflictError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.tools.update_version_state(
                previous=initial,
                updated=stale_update,
            )

    async with unit_of_work_factory() as unit_of_work:
        stored = await unit_of_work.tools.get_conformance_run(run.id)
        state = await unit_of_work.tools.get_version_state(version_id)

    assert stored == (run, (case,))
    assert state == active


async def test_conformance_case_failure_rolls_back_the_complete_run(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    definition = await persist_definition(unit_of_work_factory)
    version_id = await publish(unit_of_work_factory, definition)
    run = ToolConformanceRun(
        id=ToolConformanceRunId(uuid4()),
        tool_version_id=version_id,
        status=ToolConformanceStatus.PASSED,
        started_at=NOW,
        completed_at=NOW + timedelta(milliseconds=5),
    )
    cases = tuple(
        ToolConformanceCaseResult(
            id=ToolConformanceCaseResultId(uuid4()),
            run_id=run.id,
            case_key="duplicate_case",
            status=ToolConformanceStatus.PASSED,
            duration_ms=index,
            diagnostic_code=None,
        )
        for index in range(2)
    )

    with pytest.raises(IntegrityError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.tools.add_conformance_run(run, cases)
            await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        assert await unit_of_work.tools.get_conformance_run(run.id) is None


async def test_activation_run_must_belong_to_the_exact_tool_version(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    first_definition = await persist_definition(unit_of_work_factory, tool_key="first")
    second_definition = await persist_definition(unit_of_work_factory, tool_key="second")
    first_version_id = await publish(unit_of_work_factory, first_definition)
    second_version_id = await publish(unit_of_work_factory, second_definition)
    run = ToolConformanceRun(
        id=ToolConformanceRunId(uuid4()),
        tool_version_id=first_version_id,
        status=ToolConformanceStatus.PASSED,
        started_at=NOW,
        completed_at=NOW,
    )
    case = ToolConformanceCaseResult(
        id=ToolConformanceCaseResultId(uuid4()),
        run_id=run.id,
        case_key="valid_input",
        status=ToolConformanceStatus.PASSED,
        duration_ms=0,
        diagnostic_code=None,
    )
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tools.add_conformance_run(run, (case,))
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        second_state = await unit_of_work.tools.get_version_state(second_version_id)
    assert second_state is not None
    invalid_active = second_state.activate(
        conformance_run_id=run.id,
        at=NOW + timedelta(seconds=1),
    )

    with pytest.raises(IntegrityError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.tools.update_version_state(
                previous=second_state,
                updated=invalid_active,
            )
            await unit_of_work.commit()


class IntegrationClock:
    def now(self) -> datetime:
        return NOW


class RunIdGenerator:
    def new(self) -> ToolConformanceRunId:
        return ToolConformanceRunId(uuid4())


class CaseIdGenerator:
    def new(self) -> ToolConformanceCaseResultId:
        return ToolConformanceCaseResultId(uuid4())


class FailingOutputAdapter:
    async def invoke(
        self,
        request: ToolInvocationRequest,
    ) -> ToolInvocationSuccess | ToolInvocationFailure:
        if request.arguments["value"] == "error":
            return ToolInvocationFailure("temporarily_unavailable", retryable=True)
        return ToolInvocationSuccess({"unexpected": True})

    async def reconcile(self, idempotency_key: str) -> ToolReconciliationResult:
        del idempotency_key
        return ToolReconciliationResult(found=False, output=None)


class IntegrationResolver:
    def __init__(self) -> None:
        self.adapter = FailingOutputAdapter()

    def resolve(self, adapter_key: str) -> FailingOutputAdapter | None:
        assert adapter_key == "reference.read_value.v1"
        return self.adapter


async def test_failed_conformance_is_durable_without_changing_draft_state(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    definition = await persist_definition(unit_of_work_factory)
    version_id = await publish(unit_of_work_factory, definition)
    async with unit_of_work_factory() as unit_of_work:
        stored_version = await unit_of_work.tools.get_version(version_id)
    assert stored_version is not None
    conformance = ToolConformanceRunner(
        adapter_resolver=IntegrationResolver(),
        schema_validator=Draft202012JsonSchemaValidator(),
        unit_of_work_factory=unit_of_work_factory,
        clock=IntegrationClock(),
        run_id_generator=RunIdGenerator(),
        case_id_generator=CaseIdGenerator(),
    )
    report = await RunToolConformance(
        unit_of_work_factory=unit_of_work_factory,
        runner=conformance,
    ).execute(
        RunToolConformanceCommand(
            team_id=definition.team_id,
            tool_version_id=stored_version.id,
            suite=ToolConformanceSuite(
                valid_input={"value": "valid"},
                invalid_input={},
                invalid_output={"unexpected": True},
                timeout_input={"value": "timeout"},
                declared_error_input={"value": "error"},
                expected_error_code="temporarily_unavailable",
                idempotency_input={"value": "idempotency"},
                reconciliation_input={"value": "reconcile"},
                sensitive_input={"value": "input-secret"},
                sensitive_output={"value": "output-secret"},
            ),
        )
    )

    async with unit_of_work_factory() as unit_of_work:
        persisted = await unit_of_work.tools.get_conformance_run(report.run.id)
        state = await unit_of_work.tools.get_version_state(version_id)

    assert report.run.status is ToolConformanceStatus.FAILED
    assert persisted == (report.run, report.case_results)
    assert state is not None
    assert state.status is ToolLifecycleStatus.DRAFT
    assert state.revision == 1
