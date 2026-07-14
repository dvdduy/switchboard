import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.adapters.tools.reference import (
    SearchWorkItemsAdapter,
    UpdateDueDateAdapter,
    search_work_items_manifest,
    search_work_items_suite,
    update_due_date_manifest,
    update_due_date_suite,
)
from switchboard.adapters.tools.resolver import StaticToolAdapterResolver
from switchboard.application.errors import (
    AgentTeamMismatchError,
    ToolAlreadyBoundError,
    ToolConformanceFailedError,
    ToolDefinitionAlreadyExistsError,
    ToolTeamMismatchError,
    ToolVersionStateError,
)
from switchboard.application.services.tool_conformance import (
    ToolConformanceRunner,
    ToolConformanceSuite,
)
from switchboard.application.services.tool_manifest_validation import ToolManifestValidator
from switchboard.application.use_cases.manage_tools import (
    ActivateToolVersion,
    ActivateToolVersionCommand,
    BindToolVersionToAgentVersion,
    BindToolVersionToAgentVersionCommand,
    ChangeToolLifecycleCommand,
    DeprecateToolVersion,
    DisableToolVersion,
    ListEligibleTools,
    ListEligibleToolsCommand,
    PublishToolVersion,
    PublishToolVersionCommand,
    RegisterToolDefinition,
    RegisterToolDefinitionCommand,
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
    RetryPolicyCandidate,
    ToolConformanceCaseResult,
    ToolConformanceRun,
    ToolConformanceStatus,
    ToolDefinition,
    ToolLifecycleStatus,
    ToolManifestCandidate,
    ToolVersion,
)

NOW = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class Generator[IdentifierT]:
    def __init__(self, factory: Callable[[], IdentifierT]) -> None:
        self._factory = factory

    def new(self) -> IdentifierT:
        return self._factory()


def ids[IdentifierT](
    identifier_type: Callable[[object], IdentifierT],
) -> Generator[IdentifierT]:
    return Generator(lambda: identifier_type(uuid4()))


def candidate(*, adapter_key: str = "reference.read_value.v1") -> ToolManifestCandidate:
    schema = {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    return ToolManifestCandidate(
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
        display_name="Read value",
        description="Read a deterministic value.",
        input_schema=schema,
        output_schema=schema,
        effect="read_only",
        required_scopes=("values:read",),
        timeout_ms=1_000,
        retry_policy=RetryPolicyCandidate(2, 10, ("temporarily_unavailable",)),
        idempotency="none",
        reconciliation="none",
        adapter_key=adapter_key,
    )


def register_use_case(
    factory: SqlAlchemyUnitOfWorkFactory,
) -> RegisterToolDefinition:
    return RegisterToolDefinition(
        unit_of_work_factory=factory,
        clock=FixedClock(),
        definition_ids=ids(ToolDefinitionId),
    )


def publish_use_case(factory: SqlAlchemyUnitOfWorkFactory) -> PublishToolVersion:
    return PublishToolVersion(
        unit_of_work_factory=factory,
        manifest_validator=ToolManifestValidator(Draft202012JsonSchemaValidator()),
        clock=FixedClock(),
        version_ids=ids(ToolVersionId),
    )


async def register_and_publish(
    factory: SqlAlchemyUnitOfWorkFactory,
    *,
    team_id: TeamId,
    tool_key: str,
) -> tuple[ToolDefinition, ToolVersion]:
    definition = await register_use_case(factory).execute(
        RegisterToolDefinitionCommand(team_id=team_id, tool_key=tool_key)
    )
    result = await publish_use_case(factory).execute(
        PublishToolVersionCommand(
            team_id=team_id,
            tool_definition_id=definition.id,
            manifest=candidate(adapter_key=f"reference.{tool_key}.v1"),
        )
    )
    assert result.version is not None
    return definition, result.version


async def persist_conformance(
    factory: SqlAlchemyUnitOfWorkFactory,
    version: ToolVersion,
    *,
    status: ToolConformanceStatus,
) -> ToolConformanceRun:
    run = ToolConformanceRun(
        id=ToolConformanceRunId(uuid4()),
        tool_version_id=version.id,
        status=status,
        started_at=NOW,
        completed_at=NOW,
    )
    case = ToolConformanceCaseResult(
        id=ToolConformanceCaseResultId(uuid4()),
        run_id=run.id,
        case_key="valid_input_output",
        status=status,
        duration_ms=0,
        diagnostic_code=(None if status is ToolConformanceStatus.PASSED else "invalid_output"),
    )
    async with factory() as unit_of_work:
        await unit_of_work.tools.add_conformance_run(run, (case,))
        await unit_of_work.commit()
    return run


async def activate(
    factory: SqlAlchemyUnitOfWorkFactory,
    *,
    team_id: TeamId,
    version: ToolVersion,
) -> None:
    run = await persist_conformance(factory, version, status=ToolConformanceStatus.PASSED)
    await ActivateToolVersion(
        unit_of_work_factory=factory,
        clock=FixedClock(),
    ).execute(
        ActivateToolVersionCommand(
            team_id=team_id,
            tool_version_id=version.id,
            conformance_run_id=run.id,
        )
    )


async def seed_agent(
    factory: SqlAlchemyUnitOfWorkFactory,
    *,
    team_id: TeamId,
) -> AgentVersion:
    definition = AgentDefinition(
        id=AgentDefinitionId(uuid4()),
        team_id=team_id,
        name="Tool-enabled agent",
        created_at=NOW,
    )
    version = AgentVersion(
        id=AgentVersionId(uuid4()),
        agent_definition_id=definition.id,
        version_number=1,
        context_policy=ContextPolicy(4096, 512, 256, 256, 1),
        created_at=NOW,
    )
    async with factory() as unit_of_work:
        await unit_of_work.agents.add_definition(definition)
        await unit_of_work.agents.add_version(version)
        await unit_of_work.commit()
    return version


def bind_use_case(factory: SqlAlchemyUnitOfWorkFactory) -> BindToolVersionToAgentVersion:
    return BindToolVersionToAgentVersion(
        unit_of_work_factory=factory,
        clock=FixedClock(),
        agent_version_ids=ids(AgentVersionId),
        binding_ids=ids(AgentToolBindingId),
    )


async def test_registration_is_atomic_and_team_scoped(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    use_case = register_use_case(unit_of_work_factory)

    first = await use_case.execute(
        RegisterToolDefinitionCommand(team_id=team_id, tool_key="shared_tool")
    )
    with pytest.raises(ToolDefinitionAlreadyExistsError):
        await use_case.execute(
            RegisterToolDefinitionCommand(team_id=team_id, tool_key="shared_tool")
        )
    other_team = await use_case.execute(
        RegisterToolDefinitionCommand(team_id=TeamId(uuid4()), tool_key="shared_tool")
    )

    assert first.tool_key == other_team.tool_key
    assert first.team_id != other_team.team_id


async def test_concurrent_registration_has_one_team_key_winner(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())

    async def register() -> ToolDefinition | None:
        try:
            return await register_use_case(unit_of_work_factory).execute(
                RegisterToolDefinitionCommand(team_id=team_id, tool_key="racing_tool")
            )
        except ToolDefinitionAlreadyExistsError:
            return None

    results = await asyncio.gather(register(), register())

    assert sum(result is not None for result in results) == 1


async def test_publication_validates_before_write_and_checks_ownership(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    definition = await register_use_case(unit_of_work_factory).execute(
        RegisterToolDefinitionCommand(team_id=team_id, tool_key="publish_tool")
    )
    use_case = publish_use_case(unit_of_work_factory)

    invalid = await use_case.execute(
        PublishToolVersionCommand(
            team_id=team_id,
            tool_definition_id=definition.id,
            manifest=candidate(adapter_key="https://untrusted.invalid/tool"),
        )
    )
    with pytest.raises(ToolTeamMismatchError):
        await use_case.execute(
            PublishToolVersionCommand(
                team_id=TeamId(uuid4()),
                tool_definition_id=definition.id,
                manifest=candidate(),
            )
        )

    assert not invalid.is_published
    assert invalid.diagnostics


async def test_concurrent_publication_returns_unique_versions(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    definition = await register_use_case(unit_of_work_factory).execute(
        RegisterToolDefinitionCommand(team_id=team_id, tool_key="concurrent_tool")
    )

    async def publish() -> int:
        result = await publish_use_case(unit_of_work_factory).execute(
            PublishToolVersionCommand(
                team_id=team_id,
                tool_definition_id=definition.id,
                manifest=candidate(),
            )
        )
        assert result.version is not None
        return result.version.version_number

    assert set(await asyncio.gather(publish(), publish())) == {1, 2}


async def test_failed_conformance_cannot_activate(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    _, version = await register_and_publish(
        unit_of_work_factory,
        team_id=team_id,
        tool_key="failed_tool",
    )
    run = await persist_conformance(
        unit_of_work_factory,
        version,
        status=ToolConformanceStatus.FAILED,
    )

    with pytest.raises(ToolConformanceFailedError):
        await ActivateToolVersion(
            unit_of_work_factory=unit_of_work_factory,
            clock=FixedClock(),
        ).execute(
            ActivateToolVersionCommand(
                team_id=team_id,
                tool_version_id=version.id,
                conformance_run_id=run.id,
            )
        )

    async with unit_of_work_factory() as unit_of_work:
        state = await unit_of_work.tools.get_version_state(version.id)
    assert state is not None
    assert state.status is ToolLifecycleStatus.DRAFT


async def test_lifecycle_commands_preserve_activation_proof(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    _, version = await register_and_publish(
        unit_of_work_factory,
        team_id=team_id,
        tool_key="lifecycle_tool",
    )
    await activate(unit_of_work_factory, team_id=team_id, version=version)
    deprecated = await DeprecateToolVersion(
        unit_of_work_factory=unit_of_work_factory,
        clock=FixedClock(),
    ).execute(ChangeToolLifecycleCommand(team_id=team_id, tool_version_id=version.id))
    disabled = await DisableToolVersion(
        unit_of_work_factory=unit_of_work_factory,
        clock=FixedClock(),
    ).execute(ChangeToolLifecycleCommand(team_id=team_id, tool_version_id=version.id))

    assert deprecated.status is ToolLifecycleStatus.DEPRECATED
    assert disabled.status is ToolLifecycleStatus.DISABLED
    assert disabled.activated_conformance_run_id is not None
    assert disabled.revision == 4


async def test_binding_clones_agent_and_copies_existing_bindings(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    base = await seed_agent(unit_of_work_factory, team_id=team_id)
    first_definition, first_version = await register_and_publish(
        unit_of_work_factory, team_id=team_id, tool_key="first_bound"
    )
    second_definition, second_version = await register_and_publish(
        unit_of_work_factory, team_id=team_id, tool_key="second_bound"
    )
    await activate(unit_of_work_factory, team_id=team_id, version=first_version)
    await activate(unit_of_work_factory, team_id=team_id, version=second_version)
    binder = bind_use_case(unit_of_work_factory)

    first = await binder.execute(
        BindToolVersionToAgentVersionCommand(
            team_id=team_id,
            base_agent_version_id=base.id,
            tool_version_id=first_version.id,
        )
    )
    second = await binder.execute(
        BindToolVersionToAgentVersionCommand(
            team_id=team_id,
            base_agent_version_id=first.agent_version.id,
            tool_version_id=second_version.id,
        )
    )

    async with unit_of_work_factory() as unit_of_work:
        base_bindings = await unit_of_work.tools.list_bindings(base.id)
        first_bindings = await unit_of_work.tools.list_bindings(first.agent_version.id)
        second_bindings = await unit_of_work.tools.list_bindings(second.agent_version.id)

    assert base_bindings == ()
    assert {binding.tool_definition_id for binding in first_bindings} == {first_definition.id}
    assert {binding.tool_definition_id for binding in second_bindings} == {
        first_definition.id,
        second_definition.id,
    }
    assert base.version_number == 1
    assert first.agent_version.version_number == 2
    assert second.agent_version.version_number == 3
    assert second.agent_version.context_policy == base.context_policy


async def test_binding_rejects_ineligible_duplicate_and_cross_team_tools(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    base = await seed_agent(unit_of_work_factory, team_id=team_id)
    _, draft = await register_and_publish(
        unit_of_work_factory, team_id=team_id, tool_key="draft_tool"
    )
    binder = bind_use_case(unit_of_work_factory)

    with pytest.raises(ToolVersionStateError):
        await binder.execute(BindToolVersionToAgentVersionCommand(team_id, base.id, draft.id))

    await activate(unit_of_work_factory, team_id=team_id, version=draft)
    bound = await binder.execute(BindToolVersionToAgentVersionCommand(team_id, base.id, draft.id))
    with pytest.raises(ToolAlreadyBoundError):
        await binder.execute(
            BindToolVersionToAgentVersionCommand(
                team_id,
                bound.agent_version.id,
                draft.id,
            )
        )
    await DeprecateToolVersion(
        unit_of_work_factory=unit_of_work_factory,
        clock=FixedClock(),
    ).execute(ChangeToolLifecycleCommand(team_id=team_id, tool_version_id=draft.id))
    with pytest.raises(ToolVersionStateError):
        await binder.execute(BindToolVersionToAgentVersionCommand(team_id, base.id, draft.id))
    other_team = TeamId(uuid4())
    _, other_tool = await register_and_publish(
        unit_of_work_factory,
        team_id=other_team,
        tool_key="other_team_tool",
    )
    await activate(unit_of_work_factory, team_id=other_team, version=other_tool)
    with pytest.raises(ToolTeamMismatchError):
        await binder.execute(
            BindToolVersionToAgentVersionCommand(
                team_id,
                base.id,
                other_tool.id,
            )
        )


async def test_concurrent_bindings_allocate_distinct_agent_versions(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    base = await seed_agent(unit_of_work_factory, team_id=team_id)
    _, first = await register_and_publish(
        unit_of_work_factory, team_id=team_id, tool_key="concurrent_first"
    )
    _, second = await register_and_publish(
        unit_of_work_factory, team_id=team_id, tool_key="concurrent_second"
    )
    await activate(unit_of_work_factory, team_id=team_id, version=first)
    await activate(unit_of_work_factory, team_id=team_id, version=second)

    async def bind(version: ToolVersion) -> int:
        result = await bind_use_case(unit_of_work_factory).execute(
            BindToolVersionToAgentVersionCommand(team_id, base.id, version.id)
        )
        return result.agent_version.version_number

    assert set(await asyncio.gather(bind(first), bind(second))) == {2, 3}


async def test_reference_tools_conform_bind_and_filter_by_current_eligibility(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    base = await seed_agent(unit_of_work_factory, team_id=team_id)
    resolver = StaticToolAdapterResolver(
        {
            "reference.search_work_items.v1": SearchWorkItemsAdapter(),
            "reference.update_due_date.v1": UpdateDueDateAdapter(),
        }
    )
    conformance_runner = ToolConformanceRunner(
        adapter_resolver=resolver,
        schema_validator=Draft202012JsonSchemaValidator(),
        unit_of_work_factory=unit_of_work_factory,
        clock=FixedClock(),
        run_id_generator=ids(ToolConformanceRunId),
        case_id_generator=ids(ToolConformanceCaseResultId),
    )

    async def publish_conform_activate(
        tool_key: str,
        manifest: ToolManifestCandidate,
        suite: ToolConformanceSuite,
    ) -> ToolVersion:
        definition = await register_use_case(unit_of_work_factory).execute(
            RegisterToolDefinitionCommand(team_id=team_id, tool_key=tool_key)
        )
        published = await publish_use_case(unit_of_work_factory).execute(
            PublishToolVersionCommand(team_id, definition.id, manifest)
        )
        assert published.version is not None
        report = await RunToolConformance(
            unit_of_work_factory=unit_of_work_factory,
            runner=conformance_runner,
        ).execute(
            RunToolConformanceCommand(
                team_id,
                published.version.id,
                suite,
            )
        )
        assert report.run.status is ToolConformanceStatus.PASSED
        await ActivateToolVersion(
            unit_of_work_factory=unit_of_work_factory,
            clock=FixedClock(),
        ).execute(ActivateToolVersionCommand(team_id, published.version.id, report.run.id))
        return published.version

    search = await publish_conform_activate(
        "search_work_items",
        search_work_items_manifest(),
        search_work_items_suite(),
    )
    update = await publish_conform_activate(
        "update_due_date",
        update_due_date_manifest(),
        update_due_date_suite(),
    )
    binder = bind_use_case(unit_of_work_factory)
    search_bound = await binder.execute(
        BindToolVersionToAgentVersionCommand(team_id, base.id, search.id)
    )
    fully_bound = await binder.execute(
        BindToolVersionToAgentVersionCommand(
            team_id,
            search_bound.agent_version.id,
            update.id,
        )
    )
    query = ListEligibleTools(unit_of_work_factory=unit_of_work_factory)

    assert await query.execute(ListEligibleToolsCommand(team_id, base.id)) == ()
    eligible = await query.execute(ListEligibleToolsCommand(team_id, fully_bound.agent_version.id))
    assert [tool.definition.tool_key for tool in eligible] == [
        "search_work_items",
        "update_due_date",
    ]

    await DeprecateToolVersion(
        unit_of_work_factory=unit_of_work_factory,
        clock=FixedClock(),
    ).execute(ChangeToolLifecycleCommand(team_id, search.id))
    eligible_after_deprecation = await query.execute(
        ListEligibleToolsCommand(team_id, fully_bound.agent_version.id)
    )
    assert [tool.definition.tool_key for tool in eligible_after_deprecation] == ["update_due_date"]

    await DisableToolVersion(
        unit_of_work_factory=unit_of_work_factory,
        clock=FixedClock(),
    ).execute(ChangeToolLifecycleCommand(team_id, update.id))
    assert (
        await query.execute(ListEligibleToolsCommand(team_id, fully_bound.agent_version.id)) == ()
    )

    with pytest.raises(AgentTeamMismatchError):
        await query.execute(ListEligibleToolsCommand(TeamId(uuid4()), fully_bound.agent_version.id))


async def test_eligible_query_excludes_active_binding_backed_by_failed_run(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    agent = await seed_agent(unit_of_work_factory, team_id=team_id)
    definition, version = await register_and_publish(
        unit_of_work_factory,
        team_id=team_id,
        tool_key="failed_active_tool",
    )
    failed_run = await persist_conformance(
        unit_of_work_factory,
        version,
        status=ToolConformanceStatus.FAILED,
    )
    async with unit_of_work_factory() as unit_of_work:
        draft = await unit_of_work.tools.get_version_state(version.id)
        assert draft is not None
        artificially_active = draft.activate(
            conformance_run_id=failed_run.id,
            at=NOW,
        )
        await unit_of_work.tools.update_version_state(
            previous=draft,
            updated=artificially_active,
        )
        await unit_of_work.tools.add_binding(
            AgentToolBinding(
                id=AgentToolBindingId(uuid4()),
                agent_version_id=agent.id,
                tool_definition_id=definition.id,
                tool_version_id=version.id,
                created_at=NOW,
            )
        )
        await unit_of_work.commit()

    eligible = await ListEligibleTools(unit_of_work_factory=unit_of_work_factory).execute(
        ListEligibleToolsCommand(team_id, agent.id)
    )

    assert eligible == ()
