from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.application.errors import ToolInvocationLifecycleConflictError
from switchboard.domain.identifiers import (
    TeamId,
    ToolDefinitionId,
    ToolInvocationId,
    ToolVersionId,
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

NOW = datetime(2026, 7, 14, 23, 0, tzinfo=UTC)


def manifest() -> ToolManifest:
    schema = {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    return ToolManifest(
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
        display_name="Search work items",
        description="Search deterministic work items.",
        input_schema=schema,
        output_schema=schema,
        effect=ToolEffect.READ_ONLY,
        required_scopes=("work_items:read",),
        timeout_ms=1_000,
        retry_policy=RetryPolicy(1, 0, ()),
        idempotency=IdempotencyMode.NONE,
        reconciliation=ReconciliationMode.NONE,
        adapter_key="reference.search_work_items.v1",
    )


async def persist_tool(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    team_id: TeamId,
) -> tuple[ToolDefinition, ToolVersionId]:
    definition = ToolDefinition(
        id=ToolDefinitionId(uuid4()),
        team_id=team_id,
        tool_key=f"search_{uuid4().hex[:8]}",
        created_at=NOW,
    )
    version_id = ToolVersionId(uuid4())
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tools.add_definition(definition)
        await unit_of_work.tools.add_next_version(
            tool_version_id=version_id,
            tool_definition_id=definition.id,
            manifest=manifest(),
            created_at=NOW,
        )
        await unit_of_work.commit()
    return definition, version_id


async def pending_invocation(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> ToolInvocation:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None
    definition, version_id = await persist_tool(
        unit_of_work_factory,
        team_id=conversation.team_id,
    )
    return ToolInvocation(
        id=ToolInvocationId(uuid4()),
        turn_id=turn.id,
        attempt_id=attempt.id,
        invocation_number=1,
        tool_definition_id=definition.id,
        tool_version_id=version_id,
        arguments={"query": "overdue"},
        idempotency_key=f"invocation:{uuid4()}",
        authorized_scopes=("work_items:read",),
        status=ToolInvocationStatus.PENDING,
        created_at=NOW,
    )


async def test_invocation_round_trip_and_focused_lifecycle_updates(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    pending = await pending_invocation(unit_of_work_factory)
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tool_invocations.add(pending)
        await unit_of_work.commit()

    running = pending.start(at=NOW + timedelta(seconds=1))
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tool_invocations.update_lifecycle(
            previous=pending,
            updated=running,
        )
        await unit_of_work.commit()

    succeeded = running.succeed(
        at=NOW + timedelta(seconds=2),
        result={"items": [{"id": "WI-1"}]},
    )
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tool_invocations.update_lifecycle(
            previous=running,
            updated=succeeded,
        )
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        stored = await unit_of_work.tool_invocations.get(pending.id)
        for_turn = await unit_of_work.tool_invocations.list_for_turn(pending.turn_id)

    assert stored == succeeded
    assert for_turn == (succeeded,)


async def test_stale_invocation_lifecycle_update_is_rejected(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    pending = await pending_invocation(unit_of_work_factory)
    running = pending.start(at=NOW + timedelta(seconds=1))
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tool_invocations.add(pending)
        await unit_of_work.commit()
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tool_invocations.update_lifecycle(
            previous=pending,
            updated=running,
        )
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        with pytest.raises(ToolInvocationLifecycleConflictError):
            await unit_of_work.tool_invocations.update_lifecycle(
                previous=pending,
                updated=running,
            )


async def test_database_rejects_second_invocation_for_one_attempt(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    first = await pending_invocation(unit_of_work_factory)
    second = ToolInvocation(
        id=ToolInvocationId(uuid4()),
        turn_id=first.turn_id,
        attempt_id=first.attempt_id,
        invocation_number=1,
        tool_definition_id=first.tool_definition_id,
        tool_version_id=first.tool_version_id,
        arguments={"query": "second"},
        idempotency_key=f"invocation:{uuid4()}",
        authorized_scopes=first.authorized_scopes,
        status=ToolInvocationStatus.PENDING,
        created_at=NOW,
    )

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.tool_invocations.add(first)
        with pytest.raises(IntegrityError):
            await unit_of_work.tool_invocations.add(second)


async def test_database_rejects_attempt_owned_by_another_turn(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    invocation = await pending_invocation(unit_of_work_factory)
    _, other_attempt = await seed_turn(unit_of_work_factory, now=NOW)
    mismatched = replace(
        invocation,
        id=ToolInvocationId(uuid4()),
        attempt_id=other_attempt.id,
        idempotency_key=f"invocation:{uuid4()}",
    )

    async with unit_of_work_factory() as unit_of_work:
        with pytest.raises(IntegrityError):
            await unit_of_work.tool_invocations.add(mismatched)


async def test_database_rejects_version_owned_by_another_tool_definition(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    invocation = await pending_invocation(unit_of_work_factory)
    async with unit_of_work_factory() as unit_of_work:
        turn = await unit_of_work.turns.get(invocation.turn_id)
        assert turn is not None
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
        assert conversation is not None
    other_definition, _ = await persist_tool(
        unit_of_work_factory,
        team_id=conversation.team_id,
    )
    mismatched = replace(
        invocation,
        id=ToolInvocationId(uuid4()),
        tool_definition_id=other_definition.id,
        idempotency_key=f"invocation:{uuid4()}",
    )

    async with unit_of_work_factory() as unit_of_work:
        with pytest.raises(IntegrityError):
            await unit_of_work.tool_invocations.add(mismatched)
