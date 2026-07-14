"""Adversarial revalidation and concurrency at the approval boundary."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from switchboard.adapters.persistence.schema import tool_invocations
from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.application.errors import (
    ApprovalDecisionConflictError,
    ApprovalRevalidationError,
)
from switchboard.application.ports.tool_adapter import (
    ToolInvocationFailure,
    ToolInvocationRequest,
    ToolInvocationResult,
)
from switchboard.application.use_cases.manage_approvals import DecideApprovalCommand
from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.command_receipts import ApprovalDecision
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.identifiers import ActorId
from switchboard.domain.tool_invocations import ToolInvocationStatus
from tests.integration.test_approval_api import (
    CountingDueDateAdapter,
    approval_services,
    paused_mutation,
)
from tests.integration.test_run_turn import NOW


class BlockingDueDateAdapter(CountingDueDateAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return await self._delegate.invoke(request)


class DeclaredFailureAdapter(CountingDueDateAdapter):
    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        del request
        self.calls += 1
        return ToolInvocationFailure("temporarily_unavailable", retryable=True)


def approve_command(*, team_id, approval_id, key: str) -> DecideApprovalCommand:
    return DecideApprovalCommand(
        team_id=team_id,
        actor_id=ActorId(uuid4()),
        approval_id=approval_id,
        decision=ApprovalDecision.APPROVE,
        idempotency_key=key,
    )


@pytest.mark.parametrize("tamper", ["arguments", "disabled"])
async def test_changed_action_or_disabled_version_blocks_resume(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    database_engine: AsyncEngine,
    tamper: str,
) -> None:
    team_id, turn, _, approval_id, version = await paused_mutation(unit_of_work_factory)
    adapter = CountingDueDateAdapter()
    services = approval_services(unit_of_work_factory, adapter)
    async with unit_of_work_factory() as unit_of_work:
        approval = await unit_of_work.approvals.get_request(approval_id)
    assert approval is not None

    if tamper == "arguments":
        async with database_engine.begin() as connection:
            await connection.execute(
                update(tool_invocations)
                .where(tool_invocations.c.id == approval.invocation_id)
                .values(arguments={"work_item_id": "WI-1", "due_date": "2026-08-01"})
            )
    else:
        async with unit_of_work_factory() as unit_of_work:
            state = await unit_of_work.tools.get_version_state(version.id)
            assert state is not None
            await unit_of_work.tools.update_version_state(
                previous=state,
                updated=state.disable(at=NOW),
            )
            await unit_of_work.commit()

    with pytest.raises(ApprovalRevalidationError):
        await services.manage.decide(
            approve_command(team_id=team_id, approval_id=approval_id, key=f"tamper-{tamper}")
        )

    async with unit_of_work_factory() as unit_of_work:
        invocation = await unit_of_work.tool_invocations.get(approval.invocation_id)
        events = await unit_of_work.turns.list_events(turn_id=turn.id, after_sequence=0, limit=20)
    assert invocation is not None
    assert invocation.status is ToolInvocationStatus.AWAITING_CONFIRMATION
    assert adapter.calls == 0
    assert ExecutionEventKind.TOOL_STARTED not in {event.kind for event in events}


async def test_concurrent_same_command_has_one_resume_and_one_dispatch(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id, _, _, approval_id, _ = await paused_mutation(unit_of_work_factory)
    adapter = BlockingDueDateAdapter()
    services = approval_services(unit_of_work_factory, adapter)
    command = approve_command(
        team_id=team_id,
        approval_id=approval_id,
        key="concurrent-replay",
    )

    first = asyncio.create_task(services.manage.decide(command))
    await asyncio.wait_for(adapter.entered.wait(), timeout=2)
    async with unit_of_work_factory() as unit_of_work:
        approval = await unit_of_work.approvals.get_request(approval_id)
        assert approval is not None
        invocation = await unit_of_work.tool_invocations.get(approval.invocation_id)
        assert invocation is not None
        events = await unit_of_work.turns.list_events(
            turn_id=invocation.turn_id,
            after_sequence=0,
            limit=20,
        )
    assert approval.status is ApprovalStatus.CONSUMED
    assert invocation.status is ToolInvocationStatus.RUNNING
    assert events[-1].kind is ExecutionEventKind.TOOL_STARTED
    replay = await asyncio.wait_for(services.manage.decide(command), timeout=2)
    assert replay.approval.status is ApprovalStatus.CONSUMED
    assert replay.invocation_status is ToolInvocationStatus.RUNNING
    adapter.release.set()
    completed = await first

    assert completed.invocation_status is ToolInvocationStatus.SUCCEEDED
    assert adapter.calls == 1


async def test_approve_reject_race_has_one_durable_winner(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id, _, _, approval_id, _ = await paused_mutation(unit_of_work_factory)
    adapter = CountingDueDateAdapter()
    services = approval_services(unit_of_work_factory, adapter)
    approve = approve_command(team_id=team_id, approval_id=approval_id, key="race-approve")
    reject = DecideApprovalCommand(
        team_id=team_id,
        actor_id=ActorId(uuid4()),
        approval_id=approval_id,
        decision=ApprovalDecision.REJECT,
        idempotency_key="race-reject",
    )

    outcomes = await asyncio.gather(
        services.manage.decide(approve),
        services.manage.decide(reject),
        return_exceptions=True,
    )

    successes = [item for item in outcomes if not isinstance(item, BaseException)]
    failures = [item for item in outcomes if isinstance(item, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ApprovalDecisionConflictError)
    async with unit_of_work_factory() as unit_of_work:
        approval = await unit_of_work.approvals.get_request(approval_id)
    assert approval is not None
    assert approval.status in {ApprovalStatus.CONSUMED, ApprovalStatus.REJECTED}
    assert adapter.calls == (1 if approval.status is ApprovalStatus.CONSUMED else 0)


async def test_adapter_failure_after_consumption_is_durable_and_safe(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id, turn, _, approval_id, _ = await paused_mutation(unit_of_work_factory)
    adapter = DeclaredFailureAdapter()
    services = approval_services(unit_of_work_factory, adapter)

    result = await services.manage.decide(
        approve_command(team_id=team_id, approval_id=approval_id, key="declared-failure")
    )

    assert result.approval.status is ApprovalStatus.CONSUMED
    assert result.invocation_status is ToolInvocationStatus.FAILED
    assert adapter.calls == 1
    async with unit_of_work_factory() as unit_of_work:
        events = await unit_of_work.turns.list_events(turn_id=turn.id, after_sequence=0, limit=20)
        invocation = await unit_of_work.tool_invocations.get(result.approval.invocation_id)
    assert invocation is not None
    assert invocation.failure_code == "tool.temporarily_unavailable"
    assert [event.kind for event in events][-2:] == [
        ExecutionEventKind.TOOL_FAILED,
        ExecutionEventKind.TURN_FAILED,
    ]
    assert "temporarily_unavailable" not in repr(events[-1].payload)
