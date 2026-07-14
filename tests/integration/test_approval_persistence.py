import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.application.errors import ApprovalLifecycleConflictError
from switchboard.domain.approvals import (
    ApprovalRequest,
    ApprovalStatus,
    PolicyEvaluationRecord,
)
from switchboard.domain.identifiers import (
    ActorId,
    ApprovalRequestId,
    PolicyEvaluationId,
    TeamId,
    ToolDefinitionId,
    ToolInvocationId,
    ToolVersionId,
)
from switchboard.domain.policy import (
    PolicyContext,
    PolicyEnvironment,
    evaluate_policy,
    fingerprint_action,
    summarize_action,
)
from switchboard.domain.tool_invocations import ToolInvocation, ToolInvocationStatus
from switchboard.domain.tools import (
    JSON_SCHEMA_DRAFT_2020_12,
    TOOL_MANIFEST_SCHEMA_VERSION,
    IdempotencyMode,
    ReconciliationMode,
    RetryPolicy,
    ToolDefinition,
    ToolEffect,
    ToolManifest,
)
from tests.integration.support import seed_turn

NOW = datetime(2026, 7, 15, 2, 0, tzinfo=UTC)


def mutating_manifest() -> ToolManifest:
    input_schema = {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_id": {"type": "string"},
            "due_date": {"type": "string"},
        },
        "required": ["task_id", "due_date"],
    }
    output_schema = {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": {"updated": {"type": "boolean"}},
        "required": ["updated"],
    }
    return ToolManifest(
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
        display_name="Update due date",
        description="Update one deterministic work item due date.",
        input_schema=input_schema,
        output_schema=output_schema,
        effect=ToolEffect.MUTATING,
        required_scopes=("work_items:write",),
        timeout_ms=1_000,
        retry_policy=RetryPolicy(1, 0, ()),
        idempotency=IdempotencyMode.REQUIRED,
        reconciliation=ReconciliationMode.BY_IDEMPOTENCY_KEY,
        adapter_key="reference.update_due_date.v1",
    )


async def approval_graph(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> tuple[PolicyEvaluationRecord, ApprovalRequest]:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None

    definition = ToolDefinition(
        id=ToolDefinitionId(uuid4()),
        team_id=conversation.team_id,
        tool_key=f"update_due_date_{uuid4().hex[:8]}",
        created_at=NOW,
    )
    version_id = ToolVersionId(uuid4())
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tools.add_definition(definition)
        await unit_of_work.tools.add_next_version(
            tool_version_id=version_id,
            tool_definition_id=definition.id,
            manifest=mutating_manifest(),
            created_at=NOW,
        )
        await unit_of_work.commit()

    invocation = ToolInvocation(
        id=ToolInvocationId(uuid4()),
        turn_id=turn.id,
        attempt_id=attempt.id,
        invocation_number=1,
        tool_definition_id=definition.id,
        tool_version_id=version_id,
        arguments={"task_id": "TASK-123", "due_date": "2026-07-17"},
        idempotency_key=f"invocation:{uuid4()}",
        authorized_scopes=("work_items:write",),
        status=ToolInvocationStatus.PENDING,
        created_at=NOW,
    )
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tool_invocations.add(invocation)
        await unit_of_work.commit()

    actor_id = ActorId(uuid4())
    context = PolicyContext(
        team_id=conversation.team_id,
        actor_id=actor_id,
        agent_version_id=turn.agent_version_id,
        tool_team_id=definition.team_id,
        tool_definition_id=definition.id,
        tool_version_id=version_id,
        effect=ToolEffect.MUTATING,
        required_scopes=("work_items:write",),
        granted_scopes=("work_items:write",),
        environment=PolicyEnvironment.DEVELOPMENT,
        arguments=invocation.arguments,
        is_bound=True,
        is_active=True,
        is_conformant=True,
    )
    evaluation = PolicyEvaluationRecord(
        id=PolicyEvaluationId(uuid4()),
        team_id=context.team_id,
        requester_actor_id=context.actor_id,
        agent_version_id=context.agent_version_id,
        turn_id=turn.id,
        attempt_id=attempt.id,
        invocation_id=invocation.id,
        tool_definition_id=context.tool_definition_id,
        tool_version_id=context.tool_version_id,
        effect=context.effect,
        environment=context.environment,
        required_scopes=context.required_scopes,
        granted_scopes=context.granted_scopes,
        evaluation=evaluate_policy(context),
        fingerprint=fingerprint_action(context),
        evaluated_at=NOW,
    )
    approval = ApprovalRequest(
        id=ApprovalRequestId(uuid4()),
        team_id=evaluation.team_id,
        policy_evaluation_id=evaluation.id,
        invocation_id=invocation.id,
        requester_actor_id=evaluation.requester_actor_id,
        fingerprint=evaluation.fingerprint,
        safe_summary=summarize_action(context),
        status=ApprovalStatus.PENDING,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.approvals.add_evaluation(evaluation)
        await unit_of_work.approvals.add_request(approval)
        await unit_of_work.commit()
    return evaluation, approval


async def test_policy_evaluation_and_approval_round_trip_with_cas_lifecycle(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    evaluation, pending = await approval_graph(unit_of_work_factory)
    actor_id = ActorId(uuid4())
    approved = pending.approve(actor_id=actor_id, at=NOW + timedelta(minutes=1))
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.approvals.update_lifecycle(previous=pending, updated=approved)
        await unit_of_work.commit()

    consumed = approved.consume(at=NOW + timedelta(minutes=2))
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.approvals.update_lifecycle(previous=approved, updated=consumed)
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        stored_evaluation = await unit_of_work.approvals.get_evaluation(evaluation.id)
        stored_approval = await unit_of_work.approvals.get_request(pending.id)
        evaluations = await unit_of_work.approvals.list_evaluations_for_invocation(
            pending.invocation_id
        )
        approvals = await unit_of_work.approvals.list_requests_for_invocation(pending.invocation_id)

    assert stored_evaluation == evaluation
    assert stored_approval == consumed
    assert evaluations == (evaluation,)
    assert approvals == (consumed,)


async def test_stale_approval_decision_is_rejected(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    _, pending = await approval_graph(unit_of_work_factory)
    approved = pending.approve(actor_id=ActorId(uuid4()), at=NOW + timedelta(minutes=1))
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.approvals.update_lifecycle(previous=pending, updated=approved)
        await unit_of_work.commit()

    rejected = pending.reject(actor_id=ActorId(uuid4()), at=NOW + timedelta(minutes=1))
    async with unit_of_work_factory() as unit_of_work:
        with pytest.raises(ApprovalLifecycleConflictError):
            await unit_of_work.approvals.update_lifecycle(previous=pending, updated=rejected)


async def test_database_allows_only_one_active_approval_per_invocation(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    _, first = await approval_graph(unit_of_work_factory)
    second = replace(first, id=ApprovalRequestId(uuid4()))

    async with unit_of_work_factory() as unit_of_work:
        with pytest.raises(IntegrityError):
            await unit_of_work.approvals.add_request(second)


async def test_database_rejects_approval_with_changed_owner_or_fingerprint(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    _, approval = await approval_graph(unit_of_work_factory)
    mismatched = replace(
        approval,
        id=ApprovalRequestId(uuid4()),
        team_id=TeamId(uuid4()),
    )

    async with unit_of_work_factory() as unit_of_work:
        with pytest.raises(IntegrityError):
            await unit_of_work.approvals.add_request(mismatched)


async def test_database_rejects_summary_for_another_tool(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    _, approval = await approval_graph(unit_of_work_factory)
    mismatched = replace(
        approval,
        id=ApprovalRequestId(uuid4()),
        safe_summary=replace(
            approval.safe_summary,
            tool_definition_id=ToolDefinitionId(uuid4()),
        ),
    )

    async with unit_of_work_factory() as unit_of_work:
        with pytest.raises(IntegrityError):
            await unit_of_work.approvals.add_request(mismatched)


async def test_concurrent_opposite_decisions_have_one_cas_winner(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    _, pending = await approval_graph(unit_of_work_factory)
    barrier = asyncio.Barrier(2)

    async def decide(*, approve: bool) -> ApprovalStatus | None:
        async with unit_of_work_factory() as unit_of_work:
            stored = await unit_of_work.approvals.get_request(pending.id)
            assert stored is not None
            updated = (
                stored.approve(actor_id=ActorId(uuid4()), at=NOW + timedelta(minutes=1))
                if approve
                else stored.reject(actor_id=ActorId(uuid4()), at=NOW + timedelta(minutes=1))
            )
            await barrier.wait()
            try:
                await unit_of_work.approvals.update_lifecycle(
                    previous=stored,
                    updated=updated,
                )
                await unit_of_work.commit()
            except ApprovalLifecycleConflictError:
                return None
            return updated.status

    outcomes = await asyncio.gather(decide(approve=True), decide(approve=False))

    assert sum(outcome is not None for outcome in outcomes) == 1
    async with unit_of_work_factory() as unit_of_work:
        stored = await unit_of_work.approvals.get_request(pending.id)
    assert stored is not None
    assert stored.status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}
