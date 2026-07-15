"""Guarded deterministic environment setup for the Phase 1 local demo."""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from switchboard.adapters.persistence.schema import metadata
from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.adapters.tools.reference import (
    DEFAULT_WORK_ITEMS,
    SearchWorkItemsAdapter,
    UpdateDueDateAdapter,
    search_work_items_manifest,
    search_work_items_suite,
    update_due_date_manifest,
    update_due_date_suite,
)
from switchboard.adapters.tools.resolver import StaticToolAdapterResolver
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
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
    PublishToolVersion,
    PublishToolVersionCommand,
    RegisterToolDefinition,
    RegisterToolDefinitionCommand,
    RunToolConformance,
    RunToolConformanceCommand,
)
from switchboard.bootstrap.config import Settings, load_settings
from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.context import ContextPolicy
from switchboard.domain.identifiers import (
    ActorId,
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
    ToolConformanceStatus,
    ToolLifecycleStatus,
    ToolManifestCandidate,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SEEDED_AT = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
DEMO_CONTEXT_POLICY = ContextPolicy(4096, 512, 256, 256, 2)

DEMO_TEAM_ID = TeamId(UUID("10000000-0000-4000-8000-000000000001"))
DEMO_ACTOR_ID = ActorId(UUID("10000000-0000-4000-8000-000000000002"))
DEMO_AGENT_DEFINITION_ID = AgentDefinitionId(UUID("20000000-0000-4000-8000-000000000001"))
DEMO_BASE_AGENT_VERSION_ID = AgentVersionId(UUID("20000000-0000-4000-8000-000000000002"))
DEMO_SEARCH_AGENT_VERSION_ID = AgentVersionId(UUID("20000000-0000-4000-8000-000000000003"))
DEMO_AGENT_VERSION_ID = AgentVersionId(UUID("20000000-0000-4000-8000-000000000004"))
DEMO_SEARCH_DEFINITION_ID = ToolDefinitionId(UUID("30000000-0000-4000-8000-000000000001"))
DEMO_SEARCH_VERSION_ID = ToolVersionId(UUID("30000000-0000-4000-8000-000000000002"))
DEMO_UPDATE_DEFINITION_ID = ToolDefinitionId(UUID("30000000-0000-4000-8000-000000000003"))
DEMO_UPDATE_VERSION_ID = ToolVersionId(UUID("30000000-0000-4000-8000-000000000004"))
DEMO_SEARCH_CONFORMANCE_ID = ToolConformanceRunId(UUID("40000000-0000-4000-8000-000000000001"))
DEMO_UPDATE_CONFORMANCE_ID = ToolConformanceRunId(UUID("40000000-0000-4000-8000-000000000002"))

_DEMO_AGENT_VERSION_IDS = (
    DEMO_BASE_AGENT_VERSION_ID,
    DEMO_SEARCH_AGENT_VERSION_ID,
    DEMO_AGENT_VERSION_ID,
)
_DEMO_TOOL_DEFINITION_IDS = (DEMO_SEARCH_DEFINITION_ID, DEMO_UPDATE_DEFINITION_ID)
_DEMO_TOOL_VERSION_IDS = (DEMO_SEARCH_VERSION_ID, DEMO_UPDATE_VERSION_ID)
_DEMO_CONFORMANCE_IDS = (DEMO_SEARCH_CONFORMANCE_ID, DEMO_UPDATE_CONFORMANCE_ID)
_DEMO_BINDING_IDS = tuple(
    AgentToolBindingId(UUID(f"50000000-0000-4000-8000-{index:012d}")) for index in range(1, 4)
)
_DEMO_CASE_IDS = tuple(
    ToolConformanceCaseResultId(UUID(f"60000000-0000-4000-8000-{index:012d}"))
    for index in range(1, 17)
)


class DemoEnvironmentError(RuntimeError):
    """Base error for safe demo-environment operations."""


class UnsafeDemoResetError(DemoEnvironmentError):
    """Raised when reset is aimed outside the permitted local/test boundary."""


class DemoSeedConflictError(DemoEnvironmentError):
    """Raised when deterministic seed records are present but incomplete or changed."""


class DemoEnvironmentValidationError(DemoEnvironmentError):
    """Raised when the database schema is not at the expected migration head."""


@dataclass(frozen=True, slots=True)
class DemoSeedStatus:
    ready: bool
    present_record_count: int
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DemoEnvironmentReport:
    migration_revision: str
    seed: DemoSeedStatus


@dataclass(frozen=True, slots=True)
class DemoSeedResult:
    team_id: TeamId
    actor_id: ActorId
    agent_version_id: AgentVersionId
    tool_keys: tuple[str, ...]
    reference_work_item_ids: tuple[str, ...]


class FixedClock:
    """Return the stable timestamp used by deterministic seed records."""

    def now(self) -> datetime:
        return SEEDED_AT


class SequenceIdGenerator[IdentifierT]:
    """Return a finite declared sequence of stable identifiers."""

    def __init__(self, values: tuple[IdentifierT, ...]) -> None:
        if not values:
            raise ValueError("identifier sequence must not be empty")
        self._values = values
        self._index = 0

    def new(self) -> IdentifierT:
        if self._index >= len(self._values):
            raise RuntimeError("deterministic identifier sequence is exhausted")
        value = self._values[self._index]
        self._index += 1
        return value


def require_safe_demo_reset(settings: Settings) -> None:
    """Reject destructive reset outside an explicitly local or test database."""

    parsed = urlsplit(settings.database_url)
    database_name = parsed.path.removeprefix("/")
    allowed_host = parsed.hostname in {"localhost", "127.0.0.1", "postgres", "postgres-test"}
    allowed_database = database_name == "switchboard" or database_name.endswith("_test")
    if settings.environment not in {"local", "test"} or not allowed_host or not allowed_database:
        raise UnsafeDemoResetError(
            "demo writes require environment local/test and a local switchboard or *_test database"
        )


async def validate_demo_environment(
    engine: AsyncEngine,
    unit_of_work_factory: UnitOfWorkFactory,
) -> DemoEnvironmentReport:
    """Verify migration state and report whether deterministic seed data is ready."""

    configuration = Config(str(PROJECT_ROOT / "alembic.ini"))
    configuration.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    expected_head = ScriptDirectory.from_config(configuration).get_current_head()
    if expected_head is None:
        raise DemoEnvironmentValidationError("migration history has no single head")

    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT version_num FROM alembic_version"))
        actual_revision = result.scalar_one_or_none()
    if actual_revision != expected_head:
        raise DemoEnvironmentValidationError(
            f"database migration is {actual_revision or 'missing'}; expected {expected_head}"
        )

    return DemoEnvironmentReport(
        migration_revision=expected_head,
        seed=await inspect_demo_seed(unit_of_work_factory),
    )


async def inspect_demo_seed(unit_of_work_factory: UnitOfWorkFactory) -> DemoSeedStatus:
    """Inspect fixed seed identities without changing database state."""

    issues: list[str] = []
    present = 0
    async with unit_of_work_factory() as unit_of_work:
        definition = await unit_of_work.agents.get_definition(DEMO_AGENT_DEFINITION_ID)
        if definition is not None:
            present += 1
            if definition.team_id != DEMO_TEAM_ID or definition.name != "Switchboard Phase 1 Demo":
                issues.append("agent definition differs from deterministic seed")
        else:
            issues.append("agent definition is missing")

        for index, agent_version_id in enumerate(_DEMO_AGENT_VERSION_IDS, start=1):
            version = await unit_of_work.agents.get_version(agent_version_id)
            if version is None:
                issues.append(f"agent version {index} is missing")
                continue
            present += 1
            if (
                version.agent_definition_id != DEMO_AGENT_DEFINITION_ID
                or version.version_number != index
                or version.context_policy != DEMO_CONTEXT_POLICY
            ):
                issues.append(f"agent version {index} differs from deterministic seed")

        for definition_id, key in zip(
            _DEMO_TOOL_DEFINITION_IDS,
            ("search_work_items", "update_due_date"),
            strict=True,
        ):
            tool_definition = await unit_of_work.tools.get_definition(definition_id)
            if tool_definition is None:
                issues.append(f"tool definition {key} is missing")
                continue
            present += 1
            if tool_definition.team_id != DEMO_TEAM_ID or tool_definition.tool_key != key:
                issues.append(f"tool definition {key} differs from deterministic seed")

        for tool_version_id, expected_definition_id in zip(
            _DEMO_TOOL_VERSION_IDS,
            _DEMO_TOOL_DEFINITION_IDS,
            strict=True,
        ):
            tool_version = await unit_of_work.tools.get_version(tool_version_id)
            state = await unit_of_work.tools.get_version_state(tool_version_id)
            if tool_version is None or state is None:
                issues.append(f"tool version {tool_version_id} is missing")
                continue
            present += 2
            if tool_version.tool_definition_id != expected_definition_id:
                issues.append(f"tool version {tool_version_id} has the wrong definition")
            if state.status is not ToolLifecycleStatus.ACTIVE:
                issues.append(f"tool version {tool_version_id} is not active")

        for run_id, tool_version_id in zip(
            _DEMO_CONFORMANCE_IDS,
            _DEMO_TOOL_VERSION_IDS,
            strict=True,
        ):
            stored = await unit_of_work.tools.get_conformance_run(run_id)
            if stored is None:
                issues.append(f"conformance run {run_id} is missing")
                continue
            present += 1
            run, cases = stored
            if (
                run.tool_version_id != tool_version_id
                or run.status is not ToolConformanceStatus.PASSED
                or len(cases) != 8
                or any(case.status is not ToolConformanceStatus.PASSED for case in cases)
            ):
                issues.append(f"conformance run {run_id} did not pass the expected suite")

        bindings = await unit_of_work.tools.list_bindings(DEMO_AGENT_VERSION_ID)
        present += len(bindings)
        bound_versions = {binding.tool_version_id for binding in bindings}
        if bound_versions != set(_DEMO_TOOL_VERSION_IDS):
            issues.append("final agent version does not bind both exact tool versions")

        eligible = await unit_of_work.tools.list_eligible_for_agent(
            team_id=DEMO_TEAM_ID,
            agent_version_id=DEMO_AGENT_VERSION_ID,
        )
        if tuple(tool.definition.tool_key for tool in eligible) != (
            "search_work_items",
            "update_due_date",
        ):
            issues.append("final agent version does not expose both eligible tools")

    return DemoSeedStatus(ready=not issues, present_record_count=present, issues=tuple(issues))


async def reset_demo_environment(engine: AsyncEngine, settings: Settings) -> None:
    """Delete all domain data after enforcing the local/test safety boundary."""

    require_safe_demo_reset(settings)
    table_names = ", ".join(f'"{table.name}"' for table in reversed(metadata.sorted_tables))
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


async def seed_demo_environment(unit_of_work_factory: UnitOfWorkFactory) -> DemoSeedResult:
    """Create or verify the deterministic Phase 1 demo control-plane records."""

    current = await inspect_demo_seed(unit_of_work_factory)
    if current.ready:
        return _seed_result()
    if current.present_record_count:
        raise DemoSeedConflictError(
            "deterministic seed is partial or changed; run the guarded reset before seeding"
        )

    clock = FixedClock()
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.agents.add_definition(
            AgentDefinition(
                id=DEMO_AGENT_DEFINITION_ID,
                team_id=DEMO_TEAM_ID,
                name="Switchboard Phase 1 Demo",
                created_at=clock.now(),
            )
        )
        await unit_of_work.agents.add_version(
            AgentVersion(
                id=DEMO_BASE_AGENT_VERSION_ID,
                agent_definition_id=DEMO_AGENT_DEFINITION_ID,
                version_number=1,
                context_policy=DEMO_CONTEXT_POLICY,
                created_at=clock.now(),
            )
        )
        await unit_of_work.commit()

    schema_validator = Draft202012JsonSchemaValidator()
    manifest_validator = ToolManifestValidator(schema_validator)
    adapters = StaticToolAdapterResolver(
        {
            "reference.search_work_items.v1": SearchWorkItemsAdapter(),
            "reference.update_due_date.v1": UpdateDueDateAdapter(),
        }
    )
    case_ids = SequenceIdGenerator(_DEMO_CASE_IDS)
    binding_ids = SequenceIdGenerator(_DEMO_BINDING_IDS)

    search_version = await _publish_conform_activate(
        unit_of_work_factory=unit_of_work_factory,
        clock=clock,
        adapters=adapters,
        schema_validator=schema_validator,
        manifest_validator=manifest_validator,
        definition_id=DEMO_SEARCH_DEFINITION_ID,
        version_id=DEMO_SEARCH_VERSION_ID,
        run_id=DEMO_SEARCH_CONFORMANCE_ID,
        case_ids=case_ids,
        tool_key="search_work_items",
        manifest=search_work_items_manifest(),
        suite=search_work_items_suite(),
    )
    update_version = await _publish_conform_activate(
        unit_of_work_factory=unit_of_work_factory,
        clock=clock,
        adapters=adapters,
        schema_validator=schema_validator,
        manifest_validator=manifest_validator,
        definition_id=DEMO_UPDATE_DEFINITION_ID,
        version_id=DEMO_UPDATE_VERSION_ID,
        run_id=DEMO_UPDATE_CONFORMANCE_ID,
        case_ids=case_ids,
        tool_key="update_due_date",
        manifest=update_due_date_manifest(),
        suite=update_due_date_suite(),
    )

    binder = BindToolVersionToAgentVersion(
        unit_of_work_factory=unit_of_work_factory,
        clock=clock,
        agent_version_ids=SequenceIdGenerator(
            (DEMO_SEARCH_AGENT_VERSION_ID, DEMO_AGENT_VERSION_ID)
        ),
        binding_ids=binding_ids,
    )
    search_bound = await binder.execute(
        BindToolVersionToAgentVersionCommand(
            team_id=DEMO_TEAM_ID,
            base_agent_version_id=DEMO_BASE_AGENT_VERSION_ID,
            tool_version_id=search_version,
        )
    )
    await binder.execute(
        BindToolVersionToAgentVersionCommand(
            team_id=DEMO_TEAM_ID,
            base_agent_version_id=search_bound.agent_version.id,
            tool_version_id=update_version,
        )
    )

    completed = await inspect_demo_seed(unit_of_work_factory)
    if not completed.ready:
        raise DemoSeedConflictError("seed completed without producing the expected environment")
    return _seed_result()


async def _publish_conform_activate(
    *,
    unit_of_work_factory: UnitOfWorkFactory,
    clock: FixedClock,
    adapters: StaticToolAdapterResolver,
    schema_validator: Draft202012JsonSchemaValidator,
    manifest_validator: ToolManifestValidator,
    definition_id: ToolDefinitionId,
    version_id: ToolVersionId,
    run_id: ToolConformanceRunId,
    case_ids: SequenceIdGenerator[ToolConformanceCaseResultId],
    tool_key: str,
    manifest: ToolManifestCandidate,
    suite: ToolConformanceSuite,
) -> ToolVersionId:
    definition = await RegisterToolDefinition(
        unit_of_work_factory=unit_of_work_factory,
        clock=clock,
        definition_ids=SequenceIdGenerator((definition_id,)),
    ).execute(RegisterToolDefinitionCommand(team_id=DEMO_TEAM_ID, tool_key=tool_key))
    published = await PublishToolVersion(
        unit_of_work_factory=unit_of_work_factory,
        manifest_validator=manifest_validator,
        clock=clock,
        version_ids=SequenceIdGenerator((version_id,)),
    ).execute(
        PublishToolVersionCommand(
            team_id=DEMO_TEAM_ID,
            tool_definition_id=definition.id,
            manifest=manifest,
        )
    )
    if published.version is None:
        raise DemoEnvironmentError("reference tool manifest failed deterministic validation")

    runner = ToolConformanceRunner(
        adapter_resolver=adapters,
        schema_validator=schema_validator,
        unit_of_work_factory=unit_of_work_factory,
        clock=clock,
        run_id_generator=SequenceIdGenerator((run_id,)),
        case_id_generator=case_ids,
    )
    report = await RunToolConformance(
        unit_of_work_factory=unit_of_work_factory,
        runner=runner,
    ).execute(
        RunToolConformanceCommand(
            team_id=DEMO_TEAM_ID,
            tool_version_id=published.version.id,
            suite=suite,
        )
    )
    if report.run.status is not ToolConformanceStatus.PASSED:
        raise DemoEnvironmentError(f"reference tool {tool_key} failed conformance")
    await ActivateToolVersion(
        unit_of_work_factory=unit_of_work_factory,
        clock=clock,
    ).execute(
        ActivateToolVersionCommand(
            team_id=DEMO_TEAM_ID,
            tool_version_id=published.version.id,
            conformance_run_id=report.run.id,
        )
    )
    return published.version.id


def _seed_result() -> DemoSeedResult:
    return DemoSeedResult(
        team_id=DEMO_TEAM_ID,
        actor_id=DEMO_ACTOR_ID,
        agent_version_id=DEMO_AGENT_VERSION_ID,
        tool_keys=("search_work_items", "update_due_date"),
        reference_work_item_ids=tuple(item.id for item in DEFAULT_WORK_ITEMS),
    )


async def _run_command(command: str) -> dict[str, object]:
    settings = load_settings()
    if command in {"reset", "seed"}:
        require_safe_demo_reset(settings)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    unit_of_work_factory = SqlAlchemyUnitOfWorkFactory(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    try:
        report = await validate_demo_environment(engine, unit_of_work_factory)
        if command == "validate":
            return {
                "migration_revision": report.migration_revision,
                "seed_ready": report.seed.ready,
                "seed_issues": report.seed.issues,
                "model_gateway": "scripted",
                "reference_work_items": tuple(item.id for item in DEFAULT_WORK_ITEMS),
            }
        if command == "reset":
            await reset_demo_environment(engine, settings)
            return {"reset": "complete", "environment": settings.environment}
        result = await seed_demo_environment(unit_of_work_factory)
        return {
            "seed": "ready",
            "team_id": str(result.team_id),
            "actor_id": str(result.actor_id),
            "agent_version_id": str(result.agent_version_id),
            "tool_keys": result.tool_keys,
            "reference_work_items": result.reference_work_item_ids,
            "model_gateway": "scripted",
        }
    finally:
        await engine.dispose()


def main() -> None:
    """Run the guarded demo-environment command line interface."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "reset", "seed"))
    args = parser.parse_args()
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        output = asyncio.run(_run_command(args.command), loop_factory=loop_factory)
    except DemoEnvironmentError as error:
        parser.exit(1, f"demo environment error: {error}\n")
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
