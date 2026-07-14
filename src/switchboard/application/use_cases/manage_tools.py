"""Application workflows for tool registration, lifecycle, and agent binding."""

from dataclasses import dataclass

from switchboard.application.errors import (
    AgentDefinitionNotFoundError,
    AgentTeamMismatchError,
    AgentVersionNotFoundError,
    ToolAlreadyBoundError,
    ToolConformanceFailedError,
    ToolConformanceRunNotFoundError,
    ToolDefinitionAlreadyExistsError,
    ToolDefinitionNotFoundError,
    ToolTeamMismatchError,
    ToolVersionNotFoundError,
    ToolVersionStateError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.unit_of_work import UnitOfWork, UnitOfWorkFactory
from switchboard.application.services.tool_conformance import (
    ToolConformanceReport,
    ToolConformanceRunner,
    ToolConformanceSuite,
)
from switchboard.application.services.tool_manifest_validation import ToolManifestValidator
from switchboard.domain.agents import AgentVersion
from switchboard.domain.identifiers import (
    AgentToolBindingId,
    AgentVersionId,
    TeamId,
    ToolConformanceRunId,
    ToolDefinitionId,
    ToolVersionId,
)
from switchboard.domain.tools import (
    AgentToolBinding,
    EligibleTool,
    ManifestDiagnostic,
    ToolConformanceStatus,
    ToolDefinition,
    ToolLifecycleStatus,
    ToolManifestCandidate,
    ToolVersion,
    ToolVersionState,
)


@dataclass(frozen=True, slots=True)
class RegisterToolDefinitionCommand:
    team_id: TeamId
    tool_key: str


class RegisterToolDefinition:
    """Atomically reserve one normalized stable tool identity for a team."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: Clock,
        definition_ids: IdGenerator[ToolDefinitionId],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._definition_ids = definition_ids

    async def execute(self, command: RegisterToolDefinitionCommand) -> ToolDefinition:
        definition = ToolDefinition(
            id=self._definition_ids.new(),
            team_id=command.team_id,
            tool_key=command.tool_key,
            created_at=self._clock.now(),
        )
        async with self._unit_of_work_factory() as unit_of_work:
            if not await unit_of_work.tools.add_definition_if_absent(definition):
                raise ToolDefinitionAlreadyExistsError(
                    f"team {command.team_id} already has tool {definition.tool_key}"
                )
            await unit_of_work.commit()
        return definition


@dataclass(frozen=True, slots=True)
class PublishToolVersionCommand:
    team_id: TeamId
    tool_definition_id: ToolDefinitionId
    manifest: ToolManifestCandidate


@dataclass(frozen=True, slots=True)
class PublishToolVersionResult:
    version: ToolVersion | None
    diagnostics: tuple[ManifestDiagnostic, ...]

    @property
    def is_published(self) -> bool:
        return self.version is not None


class PublishToolVersion:
    """Validate manifest content and allocate one immutable version under lock."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        manifest_validator: ToolManifestValidator,
        clock: Clock,
        version_ids: IdGenerator[ToolVersionId],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._manifest_validator = manifest_validator
        self._clock = clock
        self._version_ids = version_ids

    async def execute(self, command: PublishToolVersionCommand) -> PublishToolVersionResult:
        async with self._unit_of_work_factory() as unit_of_work:
            definition = await unit_of_work.tools.get_definition(command.tool_definition_id)
            if definition is None:
                raise ToolDefinitionNotFoundError(
                    f"tool definition {command.tool_definition_id} was not found"
                )
            _require_tool_team(definition.team_id, command.team_id)

        validation = self._manifest_validator.validate(command.manifest)
        if validation.manifest is None:
            return PublishToolVersionResult(version=None, diagnostics=validation.diagnostics)

        async with self._unit_of_work_factory() as unit_of_work:
            version = await unit_of_work.tools.add_next_version(
                tool_version_id=self._version_ids.new(),
                tool_definition_id=definition.id,
                manifest=validation.manifest,
                created_at=self._clock.now(),
            )
            await unit_of_work.commit()
        return PublishToolVersionResult(version=version, diagnostics=())


@dataclass(frozen=True, slots=True)
class RunToolConformanceCommand:
    team_id: TeamId
    tool_version_id: ToolVersionId
    suite: ToolConformanceSuite


class RunToolConformance:
    """Preflight an owned draft version, then run conformance without an open UoW."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        runner: ToolConformanceRunner,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._runner = runner

    async def execute(self, command: RunToolConformanceCommand) -> ToolConformanceReport:
        async with self._unit_of_work_factory() as unit_of_work:
            version, state = await _load_owned_version(
                unit_of_work,
                team_id=command.team_id,
                tool_version_id=command.tool_version_id,
            )
            if state.status is not ToolLifecycleStatus.DRAFT:
                raise ToolVersionStateError("only a draft tool version can run conformance")

        return await self._runner.run(version=version, suite=command.suite)


@dataclass(frozen=True, slots=True)
class ActivateToolVersionCommand:
    team_id: TeamId
    tool_version_id: ToolVersionId
    conformance_run_id: ToolConformanceRunId


class ActivateToolVersion:
    """Activate a draft using a committed successful exact-version run and CAS."""

    def __init__(self, *, unit_of_work_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    async def execute(self, command: ActivateToolVersionCommand) -> ToolVersionState:
        async with self._unit_of_work_factory() as unit_of_work:
            version, state = await _load_owned_version(
                unit_of_work,
                team_id=command.team_id,
                tool_version_id=command.tool_version_id,
            )
            if state.status is not ToolLifecycleStatus.DRAFT:
                raise ToolVersionStateError("only a draft tool version can be activated")
            stored_run = await unit_of_work.tools.get_conformance_run(command.conformance_run_id)
            if stored_run is None:
                raise ToolConformanceRunNotFoundError(
                    f"conformance run {command.conformance_run_id} was not found"
                )
            run, cases = stored_run
            if (
                run.tool_version_id != version.id
                or run.status is not ToolConformanceStatus.PASSED
                or any(case.status is not ToolConformanceStatus.PASSED for case in cases)
            ):
                raise ToolConformanceFailedError(
                    "activation requires successful conformance for the exact version"
                )
            updated = state.activate(
                conformance_run_id=run.id,
                at=self._clock.now(),
            )
            await unit_of_work.tools.update_version_state(previous=state, updated=updated)
            await unit_of_work.commit()
        return updated


@dataclass(frozen=True, slots=True)
class ChangeToolLifecycleCommand:
    team_id: TeamId
    tool_version_id: ToolVersionId


class DeprecateToolVersion:
    """Deprecate one active version through revision compare-and-set."""

    def __init__(self, *, unit_of_work_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    async def execute(self, command: ChangeToolLifecycleCommand) -> ToolVersionState:
        async with self._unit_of_work_factory() as unit_of_work:
            _, state = await _load_owned_version(
                unit_of_work,
                team_id=command.team_id,
                tool_version_id=command.tool_version_id,
            )
            if state.status is not ToolLifecycleStatus.ACTIVE:
                raise ToolVersionStateError("only an active tool version can be deprecated")
            updated = state.deprecate(at=self._clock.now())
            await unit_of_work.tools.update_version_state(previous=state, updated=updated)
            await unit_of_work.commit()
        return updated


class DisableToolVersion:
    """Disable one nonterminal version through revision compare-and-set."""

    def __init__(self, *, unit_of_work_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    async def execute(self, command: ChangeToolLifecycleCommand) -> ToolVersionState:
        async with self._unit_of_work_factory() as unit_of_work:
            _, state = await _load_owned_version(
                unit_of_work,
                team_id=command.team_id,
                tool_version_id=command.tool_version_id,
            )
            if state.status is ToolLifecycleStatus.DISABLED:
                raise ToolVersionStateError("disabled tool version is terminal")
            updated = state.disable(at=self._clock.now())
            await unit_of_work.tools.update_version_state(previous=state, updated=updated)
            await unit_of_work.commit()
        return updated


@dataclass(frozen=True, slots=True)
class BindToolVersionToAgentVersionCommand:
    team_id: TeamId
    base_agent_version_id: AgentVersionId
    tool_version_id: ToolVersionId


@dataclass(frozen=True, slots=True)
class BindToolVersionToAgentVersionResult:
    agent_version: AgentVersion
    binding: AgentToolBinding


class BindToolVersionToAgentVersion:
    """Clone an immutable agent version and add one exact active tool binding."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: Clock,
        agent_version_ids: IdGenerator[AgentVersionId],
        binding_ids: IdGenerator[AgentToolBindingId],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._agent_version_ids = agent_version_ids
        self._binding_ids = binding_ids

    async def execute(
        self,
        command: BindToolVersionToAgentVersionCommand,
    ) -> BindToolVersionToAgentVersionResult:
        async with self._unit_of_work_factory() as unit_of_work:
            base_version = await unit_of_work.agents.get_version(command.base_agent_version_id)
            if base_version is None:
                raise AgentVersionNotFoundError(
                    f"agent version {command.base_agent_version_id} was not found"
                )
            agent_definition = await unit_of_work.agents.get_definition(
                base_version.agent_definition_id
            )
            if agent_definition is None:
                raise AgentDefinitionNotFoundError(
                    f"agent definition {base_version.agent_definition_id} was not found"
                )
            if agent_definition.team_id != command.team_id:
                raise AgentTeamMismatchError(
                    f"selected agent version does not belong to team {command.team_id}"
                )

            tool_version = await unit_of_work.tools.get_version(command.tool_version_id)
            if tool_version is None:
                raise ToolVersionNotFoundError(
                    f"tool version {command.tool_version_id} was not found"
                )
            tool_definition = await unit_of_work.tools.get_definition(
                tool_version.tool_definition_id
            )
            if tool_definition is None:
                raise ToolDefinitionNotFoundError(
                    f"tool definition {tool_version.tool_definition_id} was not found"
                )
            _require_tool_team(tool_definition.team_id, command.team_id)
            state = await unit_of_work.tools.get_version_state_for_update(tool_version.id)
            if state is None:
                raise ToolVersionStateError("tool version lifecycle state was not found")
            if state.status is not ToolLifecycleStatus.ACTIVE:
                raise ToolVersionStateError("only an active tool version can be bound")

            existing = await unit_of_work.tools.list_bindings(base_version.id)
            if any(binding.tool_definition_id == tool_definition.id for binding in existing):
                raise ToolAlreadyBoundError(
                    "agent version already binds this stable tool definition"
                )

            created_at = self._clock.now()
            cloned_version = await unit_of_work.agents.add_next_version_from(
                agent_version_id=self._agent_version_ids.new(),
                base_version=base_version,
                created_at=created_at,
            )
            for existing_binding in existing:
                await unit_of_work.tools.add_binding(
                    AgentToolBinding(
                        id=self._binding_ids.new(),
                        agent_version_id=cloned_version.id,
                        tool_definition_id=existing_binding.tool_definition_id,
                        tool_version_id=existing_binding.tool_version_id,
                        created_at=created_at,
                    )
                )
            selected_binding = AgentToolBinding(
                id=self._binding_ids.new(),
                agent_version_id=cloned_version.id,
                tool_definition_id=tool_definition.id,
                tool_version_id=tool_version.id,
                created_at=created_at,
            )
            await unit_of_work.tools.add_binding(selected_binding)
            await unit_of_work.commit()

        return BindToolVersionToAgentVersionResult(
            agent_version=cloned_version,
            binding=selected_binding,
        )


async def _load_owned_version(
    unit_of_work: UnitOfWork,
    *,
    team_id: TeamId,
    tool_version_id: ToolVersionId,
) -> tuple[ToolVersion, ToolVersionState]:
    version = await unit_of_work.tools.get_version(tool_version_id)
    if version is None:
        raise ToolVersionNotFoundError(f"tool version {tool_version_id} was not found")
    definition = await unit_of_work.tools.get_definition(version.tool_definition_id)
    if definition is None:
        raise ToolDefinitionNotFoundError(
            f"tool definition {version.tool_definition_id} was not found"
        )
    _require_tool_team(definition.team_id, team_id)
    state = await unit_of_work.tools.get_version_state(version.id)
    if state is None:
        raise ToolVersionStateError("tool version lifecycle state was not found")
    return version, state


def _require_tool_team(actual_team_id: TeamId, requested_team_id: TeamId) -> None:
    if actual_team_id != requested_team_id:
        raise ToolTeamMismatchError(
            f"selected tool version does not belong to team {requested_team_id}"
        )


@dataclass(frozen=True, slots=True)
class ListEligibleToolsCommand:
    team_id: TeamId
    agent_version_id: AgentVersionId


class ListEligibleTools:
    """Return active successful manifests bound to one owned pinned agent version."""

    def __init__(self, *, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def execute(self, command: ListEligibleToolsCommand) -> tuple[EligibleTool, ...]:
        async with self._unit_of_work_factory() as unit_of_work:
            agent_version = await unit_of_work.agents.get_version(command.agent_version_id)
            if agent_version is None:
                raise AgentVersionNotFoundError(
                    f"agent version {command.agent_version_id} was not found"
                )
            definition = await unit_of_work.agents.get_definition(agent_version.agent_definition_id)
            if definition is None:
                raise AgentDefinitionNotFoundError(
                    f"agent definition {agent_version.agent_definition_id} was not found"
                )
            if definition.team_id != command.team_id:
                raise AgentTeamMismatchError(
                    f"selected agent version does not belong to team {command.team_id}"
                )
            return await unit_of_work.tools.list_eligible_for_agent(
                team_id=command.team_id,
                agent_version_id=agent_version.id,
            )
