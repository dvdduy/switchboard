import json
from collections.abc import Mapping

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.bootstrap.config import Settings
from switchboard.bootstrap.demo import (
    READ_ONLY_ASSISTANT_MESSAGE_ID,
    READ_ONLY_ATTEMPT_ID,
    READ_ONLY_CONVERSATION_ID,
    READ_ONLY_INVOCATION_ID,
    READ_ONLY_RESPONSE,
    READ_ONLY_TURN_ID,
    WORKFLOW_TURN_ID,
    DemoJourneyError,
    run_read_only_journey,
)
from switchboard.bootstrap.demo_environment import (
    DEMO_AGENT_VERSION_ID,
    seed_demo_environment,
)
from switchboard.bootstrap.demo_workflow import (
    DEMO_APPROVAL_ID,
    DEMO_WORKFLOW_ID,
    WORKFLOW_RESPONSE,
    ApprovalWorkflowJourneyResult,
    run_approval_workflow_journey,
)
from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.execution_events import ExecutionEventKind
from switchboard.domain.tool_invocations import ToolInvocationStatus
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus
from switchboard.domain.workflows import WorkflowStatus


def settings(database_url: str) -> Settings:
    return Settings.model_validate(
        {
            "environment": "test",
            "database_url": database_url,
            "redis_url": "redis://localhost:6379/15",
        }
    )


async def completed_read_only_journey(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    test_database_url: str,
) -> None:
    await seed_demo_environment(unit_of_work_factory)
    await run_read_only_journey(
        unit_of_work_factory,
        settings=settings(test_database_url),
    )


async def test_read_only_journey_crosses_public_and_trusted_boundaries(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    test_database_url: str,
) -> None:
    await seed_demo_environment(unit_of_work_factory)

    result = await run_read_only_journey(
        unit_of_work_factory,
        settings=settings(test_database_url),
    )

    assert result.conversation_id == READ_ONLY_CONVERSATION_ID
    assert result.turn_id == READ_ONLY_TURN_ID
    assert result.attempt_id == READ_ONLY_ATTEMPT_ID
    assert result.agent_version_id == str(DEMO_AGENT_VERSION_ID)
    assert 0 < result.context_used_tokens <= result.context_available_tokens
    assert result.event_names == (
        "turn.started",
        "tool.started",
        "tool.completed",
        "response.delta",
        "response.delta",
        "response.delta",
        "response.delta",
        "response.delta",
        "turn.completed",
    )
    assert result.disconnect_after_sequence == 4
    assert result.reconnect_sequences == (5, 6, 7, 8, 9)
    assert result.reconstructed_response == READ_ONLY_RESPONSE
    assert result.history_sequences == (1, 2)
    assert result.measurement_environment == "test"
    assert result.time_to_first_committed_event_ms >= 0
    assert tuple(timing.stage for timing in result.stage_timings) == (
        "api_acceptance",
        "trusted_execution",
        "initial_event_stream",
        "event_reconnect",
        "history_verification",
        "total",
    )
    assert all(timing.milliseconds >= 0 for timing in result.stage_timings)

    async with unit_of_work_factory() as unit_of_work:
        turn = await unit_of_work.turns.get(READ_ONLY_TURN_ID)
        attempt = await unit_of_work.turns.get_attempt(READ_ONLY_ATTEMPT_ID)
        invocation = await unit_of_work.tool_invocations.get(READ_ONLY_INVOCATION_ID)
        messages = await unit_of_work.conversations.list_messages(READ_ONLY_CONVERSATION_ID)
        events = await unit_of_work.turns.list_events(
            turn_id=READ_ONLY_TURN_ID,
            after_sequence=0,
            limit=20,
        )

    assert turn is not None
    assert turn.status is TurnStatus.COMPLETED
    assert turn.agent_version_id == DEMO_AGENT_VERSION_ID
    assert attempt is not None
    assert attempt.status is TurnAttemptStatus.SUCCEEDED
    assert invocation is not None
    assert invocation.status is ToolInvocationStatus.SUCCEEDED
    assert invocation.result is not None
    result_items = invocation.result["items"]
    assert isinstance(result_items, tuple)
    assert result_items
    assert isinstance(result_items[0], Mapping)
    assert result_items[0]["id"] == "WI-1"
    assert messages[-1].id == READ_ONLY_ASSISTANT_MESSAGE_ID
    assert messages[-1].content == READ_ONLY_RESPONSE
    assert [event.kind for event in events[:3]] == [
        ExecutionEventKind.TURN_STARTED,
        ExecutionEventKind.TOOL_STARTED,
        ExecutionEventKind.TOOL_COMPLETED,
    ]
    safe_tool_events = json.dumps([dict(event.payload) for event in events[1:3]])
    assert "WI-1" not in safe_tool_events
    assert "launch" not in safe_tool_events


async def test_read_only_journey_requires_reset_before_repeat(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    test_database_url: str,
) -> None:
    await seed_demo_environment(unit_of_work_factory)
    await run_read_only_journey(
        unit_of_work_factory,
        settings=settings(test_database_url),
    )

    with pytest.raises(DemoJourneyError, match="already exists"):
        await run_read_only_journey(
            unit_of_work_factory,
            settings=settings(test_database_url),
        )


async def test_approval_workflow_recreates_runner_and_executes_mutations_once(
    database_engine: AsyncEngine,
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    test_database_url: str,
) -> None:
    await completed_read_only_journey(unit_of_work_factory, test_database_url)
    recreated_factory = SqlAlchemyUnitOfWorkFactory(
        async_sessionmaker(database_engine, expire_on_commit=False)
    )

    result = await run_approval_workflow_journey(
        unit_of_work_factory,
        recreated_factory=recreated_factory,
        settings=settings(test_database_url),
    )

    _assert_approval_workflow_result(result)
    async with recreated_factory() as unit_of_work:
        workflow = await unit_of_work.workflows.get(DEMO_WORKFLOW_ID)
        approval = await unit_of_work.workflow_plan_approvals.get(DEMO_APPROVAL_ID)
        invocations = await unit_of_work.tool_invocations.list_for_turn(WORKFLOW_TURN_ID)
    assert workflow is not None
    assert workflow.status is WorkflowStatus.COMPLETED
    assert approval is not None
    assert approval.status is ApprovalStatus.CONSUMED
    assert len(invocations) == 3
    assert [invocation.invocation_number for invocation in invocations] == [1, 2, 3]
    assert all(invocation.status is ToolInvocationStatus.SUCCEEDED for invocation in invocations)
    assert [invocation.arguments.get("work_item_id") for invocation in invocations[1:]] == [
        "WI-1",
        "WI-2",
    ]


async def test_approval_workflow_requires_reset_before_repeat(
    database_engine: AsyncEngine,
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    test_database_url: str,
) -> None:
    await completed_read_only_journey(unit_of_work_factory, test_database_url)
    recreated_factory = SqlAlchemyUnitOfWorkFactory(
        async_sessionmaker(database_engine, expire_on_commit=False)
    )
    await run_approval_workflow_journey(
        unit_of_work_factory,
        recreated_factory=recreated_factory,
        settings=settings(test_database_url),
    )

    with pytest.raises(DemoJourneyError, match="already exists"):
        await run_approval_workflow_journey(
            unit_of_work_factory,
            recreated_factory=recreated_factory,
            settings=settings(test_database_url),
        )


def _assert_approval_workflow_result(result: ApprovalWorkflowJourneyResult) -> None:
    assert result.workflow_id == DEMO_WORKFLOW_ID
    assert result.approval_id == DEMO_APPROVAL_ID
    assert result.discovery_calls == 1
    assert result.mutation_calls == 2
    assert result.duplicate_resume_calls == 0
    assert result.duplicate_resume_replayed
    assert result.runner_recreated
    assert result.measurement_environment == "test"
    assert result.response_text == WORKFLOW_RESPONSE
    assert result.event_names == (
        "turn.started",
        "tool.started",
        "tool.completed",
        "workflow.planned",
        "approval.required",
        "approval.resolved",
        "workflow.resumed",
        "tool.started",
        "tool.completed",
        "tool.started",
        "tool.completed",
        "workflow.terminal",
        "turn.completed",
    )
    assert result.history_sequences == (1, 2, 3, 4)
    assert tuple(timing.stage for timing in result.stage_timings) == (
        "api_continuation",
        "discovery_and_replay",
        "plan_freeze",
        "public_approval",
        "recreated_runner_resume",
        "duplicate_resume",
        "final_public_evidence",
        "total",
    )
    assert all(timing.milliseconds >= 0 for timing in result.stage_timings)
