from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    ActorId,
    AgentVersionId,
    TeamId,
    ToolDefinitionId,
    ToolInvocationId,
    ToolVersionId,
    TurnWorkflowId,
    WorkflowPlanApprovalId,
)
from switchboard.domain.policy import (
    ACTION_FINGERPRINT_VERSION,
    POLICY_VERSION,
    ActionFingerprint,
    PolicyEnvironment,
    SafeActionSummary,
)
from switchboard.domain.tools import ToolEffect
from switchboard.domain.workflow_approvals import (
    WorkflowPlanActionSummary,
    WorkflowPlanApproval,
)
from switchboard.domain.workflows import WorkflowPlanAction, fingerprint_workflow_plan

NOW = datetime(2026, 7, 15, tzinfo=UTC)


def test_workflow_plan_fingerprint_is_ordered_and_stable() -> None:
    team_id = TeamId(uuid4())
    actor_id = ActorId(uuid4())
    agent_id = AgentVersionId(uuid4())
    workflow_id = TurnWorkflowId(uuid4())
    actions = tuple(
        WorkflowPlanAction(
            step_number=step,
            invocation_id=ToolInvocationId(uuid4()),
            fingerprint=ActionFingerprint(
                version=ACTION_FINGERPRINT_VERSION,
                digest=str(step) * 64,
            ),
        )
        for step in (2, 3)
    )

    first = fingerprint_workflow_plan(
        team_id=team_id,
        requester_actor_id=actor_id,
        agent_version_id=agent_id,
        workflow_id=workflow_id,
        plan_version=1,
        environment=PolicyEnvironment.TEST,
        policy_version=POLICY_VERSION,
        actions=actions,
    )
    repeated = fingerprint_workflow_plan(
        team_id=team_id,
        requester_actor_id=actor_id,
        agent_version_id=agent_id,
        workflow_id=workflow_id,
        plan_version=1,
        environment=PolicyEnvironment.TEST,
        policy_version=POLICY_VERSION,
        actions=actions,
    )

    assert first == repeated
    with pytest.raises(DomainValidationError, match="unique increasing"):
        fingerprint_workflow_plan(
            team_id=team_id,
            requester_actor_id=actor_id,
            agent_version_id=agent_id,
            workflow_id=workflow_id,
            plan_version=1,
            environment=PolicyEnvironment.TEST,
            policy_version=POLICY_VERSION,
            actions=tuple(reversed(actions)),
        )


def test_workflow_plan_approval_requires_ordered_value_free_mutations() -> None:
    action = WorkflowPlanActionSummary(
        step_number=2,
        action=SafeActionSummary(
            tool_definition_id=ToolDefinitionId(uuid4()),
            tool_version_id=ToolVersionId(uuid4()),
            effect=ToolEffect.MUTATING,
            argument_fields=("work_item_id", "due_date"),
        ),
    )
    approval = WorkflowPlanApproval(
        id=WorkflowPlanApprovalId(uuid4()),
        workflow_id=TurnWorkflowId(uuid4()),
        team_id=TeamId(uuid4()),
        requester_actor_id=ActorId(uuid4()),
        fingerprint=fingerprint_workflow_plan(
            team_id=TeamId(uuid4()),
            requester_actor_id=ActorId(uuid4()),
            agent_version_id=AgentVersionId(uuid4()),
            workflow_id=TurnWorkflowId(uuid4()),
            plan_version=1,
            environment=PolicyEnvironment.TEST,
            policy_version=POLICY_VERSION,
            actions=(),
        ),
        safe_actions=(action,),
        status=ApprovalStatus.PENDING,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )

    assert approval.safe_actions == (action,)
    assert "work_item_id" not in repr(approval).split("argument_fields")[0]
    with pytest.raises(DomainValidationError, match="at least one"):
        replace(approval, safe_actions=())
