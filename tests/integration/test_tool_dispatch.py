from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.adapters.tools.reference import (
    SearchWorkItemsAdapter,
    search_work_items_manifest,
)
from switchboard.adapters.tools.resolver import StaticToolAdapterResolver
from switchboard.application.ports.model_gateway import CallTool
from switchboard.application.services.tool_dispatch import (
    DurableToolCallHandler,
    ToolDispatchContext,
)
from switchboard.application.services.tool_manifest_validation import ToolManifestValidator
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import (
    ActorId,
    AgentToolBindingId,
    ApprovalRequestId,
    ExecutionEventId,
    PolicyEvaluationId,
    ToolConformanceCaseResultId,
    ToolConformanceRunId,
    ToolDefinitionId,
    ToolInvocationId,
    ToolVersionId,
)
from switchboard.domain.tool_invocations import ToolInvocationStatus
from switchboard.domain.tools import (
    AgentToolBinding,
    ToolConformanceCaseResult,
    ToolConformanceRun,
    ToolConformanceStatus,
    ToolDefinition,
)
from tests.integration.support import seed_running_turn

NOW = datetime(2026, 7, 14, 23, 45, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class Generator[IdentifierT]:
    def __init__(self, factory: Callable[[], IdentifierT]) -> None:
        self._factory = factory

    def new(self) -> IdentifierT:
        return self._factory()


async def test_read_only_dispatch_persists_lifecycle_and_safe_ordered_events(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    turn, attempt = await seed_running_turn(unit_of_work_factory, now=NOW)
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None

    validation = ToolManifestValidator(Draft202012JsonSchemaValidator()).validate(
        search_work_items_manifest()
    )
    assert validation.manifest is not None
    definition = ToolDefinition(
        id=ToolDefinitionId(uuid4()),
        team_id=conversation.team_id,
        tool_key="search_work_items",
        created_at=NOW,
    )
    version_id = ToolVersionId(uuid4())
    run = ToolConformanceRun(
        id=ToolConformanceRunId(uuid4()),
        tool_version_id=version_id,
        status=ToolConformanceStatus.PASSED,
        started_at=NOW,
        completed_at=NOW,
    )
    case = ToolConformanceCaseResult(
        id=ToolConformanceCaseResultId(uuid4()),
        run_id=run.id,
        case_key="valid_input_output",
        status=ToolConformanceStatus.PASSED,
        duration_ms=0,
        diagnostic_code=None,
    )

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tools.add_definition(definition)
        version = await unit_of_work.tools.add_next_version(
            tool_version_id=version_id,
            tool_definition_id=definition.id,
            manifest=validation.manifest,
            created_at=NOW,
        )
        await unit_of_work.tools.add_conformance_run(run, (case,))
        draft = await unit_of_work.tools.get_version_state(version.id)
        assert draft is not None
        await unit_of_work.tools.update_version_state(
            previous=draft,
            updated=draft.activate(conformance_run_id=run.id, at=NOW),
        )
        await unit_of_work.tools.add_binding(
            AgentToolBinding(
                id=AgentToolBindingId(uuid4()),
                agent_version_id=turn.agent_version_id,
                tool_definition_id=definition.id,
                tool_version_id=version.id,
                created_at=NOW,
            )
        )
        await unit_of_work.commit()

    handler = DurableToolCallHandler(
        context=ToolDispatchContext(
            team_id=conversation.team_id,
            actor_id=ActorId(uuid4()),
            agent_version_id=turn.agent_version_id,
            turn_id=turn.id,
            attempt_id=attempt.id,
            granted_scopes=("work_items:read",),
        ),
        unit_of_work_factory=unit_of_work_factory,
        adapter_resolver=StaticToolAdapterResolver(
            {validation.manifest.adapter_key: SearchWorkItemsAdapter()}
        ),
        schema_validator=Draft202012JsonSchemaValidator(),
        clock=FixedClock(),
        invocation_ids=Generator(lambda: ToolInvocationId(uuid4())),
        policy_evaluation_ids=Generator(lambda: PolicyEvaluationId(uuid4())),
        approval_ids=Generator(lambda: ApprovalRequestId(uuid4())),
        event_ids=Generator(lambda: ExecutionEventId(uuid4())),
    )

    result = await handler.execute(
        CallTool(tool_version_id=version.id, arguments={"query": "launch"})
    )

    async with unit_of_work_factory() as unit_of_work:
        invocations = await unit_of_work.tool_invocations.list_for_turn(turn.id)
        events = await unit_of_work.turns.list_events(
            turn_id=turn.id,
            after_sequence=0,
            limit=10,
        )

    assert result.output["items"] == (
        {
            "id": "WI-1",
            "title": "Prepare launch checklist",
            "status": "open",
            "due_date": "2026-07-20",
        },
    )
    assert len(invocations) == 1
    assert invocations[0].status is ToolInvocationStatus.SUCCEEDED
    assert [event.kind for event in events] == [
        ExecutionEventKind.TOOL_STARTED,
        ExecutionEventKind.TOOL_COMPLETED,
    ]
    assert [event.sequence for event in events] == [1, 2]
    assert "launch" not in repr(tuple(event.payload for event in events))
    assert "WI-1" not in repr(tuple(event.payload for event in events))
