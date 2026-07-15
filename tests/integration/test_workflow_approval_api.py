from datetime import timedelta
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from switchboard.adapters.api.app import create_app
from switchboard.adapters.api.dependencies import ApprovalApiServices
from switchboard.application.services.readiness import ReadinessService
from switchboard.application.services.replay_turn_events import ReplayTurnEvents
from switchboard.application.use_cases.approve_workflow_plan import ApproveWorkflowPlan
from switchboard.application.use_cases.build_turn_context import (
    BuildTurnContext,
    BuildTurnContextCommand,
)
from switchboard.application.use_cases.cancel_workflow_plan import CancelWorkflowPlan
from switchboard.application.use_cases.continue_conversation import (
    ContinueConversation,
    ContinueConversationCommand,
)
from switchboard.application.use_cases.manage_workflow_plan_approvals import (
    ManageWorkflowPlanApprovals,
)
from switchboard.application.use_cases.run_approved_workflow import (
    RunApprovedWorkflowCommand,
)
from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.command_receipts import ApprovalDecision
from switchboard.domain.identifiers import (
    CommandReceiptId,
    ExecutionEventId,
    MessageId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.workflows import WorkflowStatus
from tests.integration.test_approval_api import (
    CountingDueDateAdapter,
    approval_services,
)
from tests.integration.test_build_turn_context import (
    CharacterTokenCounter,
    CountingSummarizer,
    SummaryIdGenerator,
)
from tests.integration.test_build_turn_context import (
    FixedClock as ContextClock,
)
from tests.integration.test_conversation_api import UnexpectedSleeper, make_test_settings
from tests.integration.test_freeze_workflow_mutation_plan import (
    planner,
    prepared_plan_command,
)
from tests.integration.test_run_approved_workflow import (
    CountingUpdateAdapter,
    workflow_runner,
)
from tests.integration.test_run_workflow_discovery import ACTOR_ID, NOW, FixedClock, Generator


def public_approval_services(factory) -> ApprovalApiServices:
    base = approval_services(factory, CountingDueDateAdapter())
    clock = FixedClock()
    event_ids = Generator(lambda: ExecutionEventId(uuid4()))
    return ApprovalApiServices(
        manage=base.manage,
        workflow_plans=ManageWorkflowPlanApprovals(
            unit_of_work_factory=factory,
            clock=clock,
            approve=ApproveWorkflowPlan(
                unit_of_work_factory=factory,
                clock=clock,
                event_ids=event_ids,
            ),
            cancel=CancelWorkflowPlan(
                unit_of_work_factory=factory,
                clock=clock,
                event_ids=event_ids,
            ),
        ),
    )


async def test_workflow_approval_api_is_safe_additive_and_history_is_multi_turn(
    unit_of_work_factory,
) -> None:
    plan_command = await prepared_plan_command(
        unit_of_work_factory,
        items=[{"id": "WI-1"}, {"id": "WI-2"}],
    )
    frozen = await planner(unit_of_work_factory).execute(plan_command)
    assert frozen.approval_id is not None
    app = create_app(
        settings=make_test_settings(),
        readiness_service=ReadinessService(probes=()),
        approval_api_services=public_approval_services(unit_of_work_factory),
        replay_turn_events=ReplayTurnEvents(
            unit_of_work_factory=unit_of_work_factory,
            sleeper=UnexpectedSleeper(),
        ),
    )
    schema = app.openapi()
    approval_paths = schema["paths"]
    assert {
        "/api/v1/approvals/{approval_id}",
        "/api/v1/approvals/{approval_id}/decisions",
    } <= approval_paths.keys()
    assert approval_paths["/api/v1/approvals/{approval_id}/decisions"]["post"][
        "responses"
    ].keys() >= {"200", "400", "404", "409", "422"}
    assert {
        "V1ApprovalDecisionRequest",
        "V1ApprovalDecisionResponse",
        "V1ApprovalResponse",
        "V1WorkflowApprovalSafeAction",
    } <= schema["components"]["schemas"].keys()
    transport = ASGITransport(app=app)
    headers = {
        "X-Team-ID": str(plan_command.team_id),
        "X-Actor-ID": str(ACTOR_ID),
        "Idempotency-Key": "approve-workflow-plan-001",
    }
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pending = await client.get(
            f"/api/v1/approvals/{frozen.approval_id}",
            headers={"X-Team-ID": str(plan_command.team_id)},
        )
        cross_team = await client.get(
            f"/api/v1/approvals/{frozen.approval_id}",
            headers={"X-Team-ID": str(uuid4())},
        )
        approved = await client.post(
            f"/api/v1/approvals/{frozen.approval_id}/decisions",
            headers=headers,
            json={"decision": ApprovalDecision.APPROVE.value},
        )
        replay = await client.post(
            f"/api/v1/approvals/{frozen.approval_id}/decisions",
            headers=headers,
            json={"decision": ApprovalDecision.APPROVE.value},
        )

    assert pending.status_code == 200
    body = pending.json()
    assert body["target_type"] == "workflow_plan"
    assert body["invocation_id"] is None
    assert body["workflow_id"] == str(frozen.workflow_id)
    assert body["mutation_count"] == 2
    assert [action["step_number"] for action in body["safe_actions"]] == [2, 3]
    assert body["safe_summary"] is None
    assert "WI-1" not in pending.text
    assert "2026-07-17" not in pending.text
    assert "digest" not in pending.text
    assert cross_team.status_code == 404
    assert approved.status_code == 200
    assert approved.json()["status"] == ApprovalStatus.APPROVED.value
    assert approved.json()["workflow_status"] == WorkflowStatus.AWAITING_CONFIRMATION.value
    assert replay.json() == approved.json()

    adapter = CountingUpdateAdapter()
    completed = await workflow_runner(unit_of_work_factory, adapter).execute(
        RunApprovedWorkflowCommand(
            team_id=plan_command.team_id,
            workflow_id=frozen.workflow_id,
        )
    )
    assert adapter.calls == 2

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        final_approval = await client.get(
            f"/api/v1/approvals/{frozen.approval_id}",
            headers={"X-Team-ID": str(plan_command.team_id)},
        )
        stream = await client.get(
            f"/api/v1/turns/{plan_command.turn_id}/events",
            headers={"X-Team-ID": str(plan_command.team_id)},
        )
    assert final_approval.json()["status"] == ApprovalStatus.CONSUMED.value
    assert final_approval.json()["workflow_id"] == str(frozen.workflow_id)
    assert stream.status_code == 200
    assert "workflow.planned" in stream.text
    assert "workflow.resumed" in stream.text
    assert "workflow.terminal" in stream.text
    assert "WI-1" not in stream.text
    assert "2026-07-17" not in stream.text
    assert "digest" not in stream.text

    async with unit_of_work_factory() as unit_of_work:
        turn = await unit_of_work.turns.get(plan_command.turn_id)
    assert turn is not None
    follow_up = await ContinueConversation(
        unit_of_work_factory=unit_of_work_factory,
        clock=ContextClock(NOW + timedelta(seconds=1)),
        message_ids=Generator(lambda: MessageId(uuid4())),
        turn_ids=Generator(lambda: TurnId(uuid4())),
        attempt_ids=Generator(lambda: TurnAttemptId(uuid4())),
        receipt_ids=Generator(lambda: CommandReceiptId(uuid4())),
    ).execute(
        ContinueConversationCommand(
            team_id=plan_command.team_id,
            conversation_id=turn.conversation_id,
            user_message="Did the workflow finish successfully?",
            idempotency_key="inspect-completed-workflow-001",
        )
    )
    counter = CharacterTokenCounter()
    context = await BuildTurnContext(
        unit_of_work_factory=unit_of_work_factory,
        token_counter=counter,
        summarizer=CountingSummarizer(counter),
        clock=ContextClock(NOW + timedelta(seconds=1)),
        summary_ids=SummaryIdGenerator(),
    ).execute(BuildTurnContextCommand(follow_up.turn_id))
    contents = tuple(item.content for item in context.items)
    assert any(completed.response_text in content for content in contents)
    assert any("Did the workflow finish successfully?" in content for content in contents)
