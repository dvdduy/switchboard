import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.adapters.tools.resolver import StaticToolAdapterResolver
from switchboard.application.errors import (
    ToolDispatchError,
    WorkflowDiscoveryConflictError,
    WorkflowDiscoveryInProgressError,
)
from switchboard.application.ports.tool_adapter import (
    ToolInvocationFailure,
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolInvocationSuccess,
    ToolReconciliationResult,
)
from switchboard.application.use_cases.run_workflow_discovery import (
    RunWorkflowDiscovery,
    RunWorkflowDiscoveryCommand,
)
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    ActorId,
    ExecutionEventId,
    PolicyEvaluationId,
    ToolInvocationId,
    TurnId,
    TurnWorkflowId,
    WorkflowStepId,
)
from switchboard.domain.tool_invocations import ToolInvocationStatus
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus
from switchboard.domain.workflows import WorkflowStatus, WorkflowStepStatus
from tests.integration.support import seed_running_turn
from tests.integration.test_run_turn import activate_tool

NOW = datetime(2026, 7, 15, tzinfo=UTC)
ACTOR_ID = ActorId(uuid4())
ADAPTER_KEY = "reference.search_work_items.v1"
OUTPUT = {
    "items": [
        {
            "id": "WI-1",
            "title": "Prepare launch checklist",
            "status": "open",
            "due_date": "2026-07-10",
        }
    ]
}


class FixedClock:
    def now(self) -> datetime:
        return NOW


class Generator[IdentifierT]:
    def __init__(self, factory: Callable[[], IdentifierT]) -> None:
        self._factory = factory

    def new(self) -> IdentifierT:
        return self._factory()


class InspectingDiscoveryAdapter:
    def __init__(
        self,
        factory: SqlAlchemyUnitOfWorkFactory,
        turn_id: TurnId,
        *,
        result: ToolInvocationResult | None = None,
    ) -> None:
        self._factory = factory
        self._turn_id = turn_id
        self._result = ToolInvocationSuccess(OUTPUT) if result is None else result
        self.calls = 0

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        self.calls += 1
        async with self._factory() as unit_of_work:
            workflow = await unit_of_work.workflows.get_for_turn(self._turn_id)
            assert workflow is not None
            steps = await unit_of_work.workflows.list_steps(workflow.id)
            assert len(steps) == 1 and steps[0].invocation_id is not None
            step = steps[0]
            invocation = await unit_of_work.tool_invocations.get(step.invocation_id)
        assert invocation is not None
        assert invocation.idempotency_key == request.idempotency_key
        assert workflow.status is WorkflowStatus.DISCOVERY_RUNNING
        assert step.status is WorkflowStepStatus.RUNNING
        assert invocation.status is ToolInvocationStatus.RUNNING
        return self._result

    async def reconcile(self, idempotency_key: str) -> ToolReconciliationResult:
        raise AssertionError(f"unexpected reconciliation for {idempotency_key}")


class BlockingDiscoveryAdapter(InspectingDiscoveryAdapter):
    def __init__(self, factory: SqlAlchemyUnitOfWorkFactory, turn_id: TurnId) -> None:
        super().__init__(factory, turn_id)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        self.started.set()
        await self.release.wait()
        return await super().invoke(request)


def discovery_service(
    factory: SqlAlchemyUnitOfWorkFactory,
    adapter: InspectingDiscoveryAdapter,
) -> RunWorkflowDiscovery:
    return RunWorkflowDiscovery(
        unit_of_work_factory=factory,
        adapter_resolver=StaticToolAdapterResolver({ADAPTER_KEY: adapter}),
        schema_validator=Draft202012JsonSchemaValidator(),
        clock=FixedClock(),
        workflow_ids=Generator(lambda: TurnWorkflowId(uuid4())),
        step_ids=Generator(lambda: WorkflowStepId(uuid4())),
        invocation_ids=Generator(lambda: ToolInvocationId(uuid4())),
        policy_evaluation_ids=Generator(lambda: PolicyEvaluationId(uuid4())),
        event_ids=Generator(lambda: ExecutionEventId(uuid4())),
    )


async def setup_command(factory: SqlAlchemyUnitOfWorkFactory):
    turn, attempt = await seed_running_turn(factory, now=NOW)
    async with factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None
    version = await activate_tool(
        factory,
        turn_agent_version_id=turn.agent_version_id,
        team_id=conversation.team_id,
    )
    return RunWorkflowDiscoveryCommand(
        team_id=conversation.team_id,
        actor_id=ACTOR_ID,
        agent_version_id=turn.agent_version_id,
        turn_id=turn.id,
        attempt_id=attempt.id,
        tool_version_id=version.id,
        arguments={"query": "critical", "limit": 10},
        granted_scopes=("work_items:read",),
    )


async def test_discovery_persists_before_dispatch_and_recreated_runner_skips_read(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    command = await setup_command(unit_of_work_factory)
    adapter = InspectingDiscoveryAdapter(unit_of_work_factory, command.turn_id)

    first = await discovery_service(unit_of_work_factory, adapter).execute(command)
    recreated = discovery_service(unit_of_work_factory, adapter)
    replay = await recreated.execute(command)

    assert first.output["items"][0]["id"] == "WI-1"
    assert first.replayed is False
    assert replay.output == first.output
    assert replay.replayed is True
    assert replay.workflow_id == first.workflow_id
    assert replay.invocation_id == first.invocation_id
    assert adapter.calls == 1

    async with unit_of_work_factory() as unit_of_work:
        workflow = await unit_of_work.workflows.get(first.workflow_id)
        steps = await unit_of_work.workflows.list_steps(first.workflow_id)
        invocation = await unit_of_work.tool_invocations.get(first.invocation_id)
        evaluations = await unit_of_work.approvals.list_evaluations_for_invocation(
            first.invocation_id
        )
        events = await unit_of_work.turns.list_events(
            turn_id=command.turn_id,
            after_sequence=0,
            limit=20,
        )

    assert workflow is not None and workflow.status is WorkflowStatus.PLANNING
    assert len(steps) == 1 and steps[0].status is WorkflowStepStatus.SUCCEEDED
    assert invocation is not None and invocation.status is ToolInvocationStatus.SUCCEEDED
    assert invocation.result == first.output
    assert len(evaluations) == 1
    assert [event.kind for event in events] == [
        ExecutionEventKind.TOOL_STARTED,
        ExecutionEventKind.TOOL_COMPLETED,
    ]


async def test_concurrent_runner_cannot_dispatch_discovery_twice(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    command = await setup_command(unit_of_work_factory)
    adapter = BlockingDiscoveryAdapter(unit_of_work_factory, command.turn_id)
    first_service = discovery_service(unit_of_work_factory, adapter)
    second_service = discovery_service(unit_of_work_factory, adapter)

    first_task = asyncio.create_task(first_service.execute(command))
    await adapter.started.wait()
    with pytest.raises(WorkflowDiscoveryInProgressError):
        await second_service.execute(command)
    adapter.release.set()
    result = await first_task

    assert result.replayed is False
    assert adapter.calls == 1


async def test_changed_discovery_command_conflicts_without_second_call(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    command = await setup_command(unit_of_work_factory)
    adapter = InspectingDiscoveryAdapter(unit_of_work_factory, command.turn_id)
    await discovery_service(unit_of_work_factory, adapter).execute(command)

    changed = RunWorkflowDiscoveryCommand(
        team_id=command.team_id,
        actor_id=command.actor_id,
        agent_version_id=command.agent_version_id,
        turn_id=command.turn_id,
        attempt_id=command.attempt_id,
        tool_version_id=command.tool_version_id,
        arguments={"query": "different"},
        granted_scopes=command.granted_scopes,
    )
    with pytest.raises(WorkflowDiscoveryConflictError):
        await discovery_service(unit_of_work_factory, adapter).execute(changed)

    assert adapter.calls == 1


async def test_invalid_discovery_is_rejected_before_intent_persistence(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    command = await setup_command(unit_of_work_factory)
    adapter = InspectingDiscoveryAdapter(unit_of_work_factory, command.turn_id)
    invalid = RunWorkflowDiscoveryCommand(
        team_id=command.team_id,
        actor_id=command.actor_id,
        agent_version_id=command.agent_version_id,
        turn_id=command.turn_id,
        attempt_id=command.attempt_id,
        tool_version_id=command.tool_version_id,
        arguments={"query": ""},
        granted_scopes=command.granted_scopes,
    )

    with pytest.raises(ToolDispatchError, match="tool_arguments_invalid"):
        await discovery_service(unit_of_work_factory, adapter).execute(invalid)

    async with unit_of_work_factory() as unit_of_work:
        assert await unit_of_work.workflows.get_for_turn(command.turn_id) is None
        assert await unit_of_work.tool_invocations.list_for_turn(command.turn_id) == ()


async def test_declared_discovery_failure_terminates_workflow_and_turn(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    command = await setup_command(unit_of_work_factory)
    adapter = InspectingDiscoveryAdapter(
        unit_of_work_factory,
        command.turn_id,
        result=ToolInvocationFailure("temporarily_unavailable", retryable=True),
    )

    with pytest.raises(ToolDispatchError, match="tool.temporarily_unavailable"):
        await discovery_service(unit_of_work_factory, adapter).execute(command)

    async with unit_of_work_factory() as unit_of_work:
        workflow = await unit_of_work.workflows.get_for_turn(command.turn_id)
        assert workflow is not None
        steps = await unit_of_work.workflows.list_steps(workflow.id)
        invocation = await unit_of_work.tool_invocations.get(steps[0].invocation_id)
        turn = await unit_of_work.turns.get(command.turn_id)
        attempt = await unit_of_work.turns.get_attempt(command.attempt_id)
        events = await unit_of_work.turns.list_events(
            turn_id=command.turn_id,
            after_sequence=0,
            limit=20,
        )

    assert workflow.status is WorkflowStatus.DISCOVERY_FAILED
    assert steps[0].status is WorkflowStepStatus.FAILED
    assert invocation is not None and invocation.status is ToolInvocationStatus.FAILED
    assert turn is not None and turn.status is TurnStatus.FAILED
    assert attempt is not None and attempt.status is TurnAttemptStatus.FAILED
    assert [event.kind for event in events] == [
        ExecutionEventKind.TOOL_STARTED,
        ExecutionEventKind.TOOL_FAILED,
        ExecutionEventKind.TURN_FAILED,
    ]
