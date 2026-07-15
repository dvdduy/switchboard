from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from switchboard.domain.errors import DomainValidationError, InvalidStateTransition
from switchboard.domain.identifiers import (
    ToolDefinitionId,
    ToolInvocationId,
    ToolVersionId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.json_values import JsonObject
from switchboard.domain.tool_invocations import (
    ToolInvocation,
    ToolInvocationStatus,
)

NOW = datetime(2026, 7, 14, 23, 0, tzinfo=UTC)


def pending_invocation(
    *,
    arguments: JsonObject | None = None,
    authorized_scopes: tuple[str, ...] = ("work_items:read",),
) -> ToolInvocation:
    return ToolInvocation(
        id=ToolInvocationId(uuid4()),
        turn_id=TurnId(uuid4()),
        attempt_id=TurnAttemptId(uuid4()),
        invocation_number=1,
        tool_definition_id=ToolDefinitionId(uuid4()),
        tool_version_id=ToolVersionId(uuid4()),
        arguments=({"query": "overdue", "filters": ["open"]} if arguments is None else arguments),
        idempotency_key=f"invocation:{uuid4()}",
        authorized_scopes=authorized_scopes,
        status=ToolInvocationStatus.PENDING,
        created_at=NOW,
    )


def test_invocation_lifecycle_freezes_arguments_and_results() -> None:
    arguments = {"query": "overdue", "filters": ["open"]}
    pending = pending_invocation(
        arguments=arguments,
        authorized_scopes=("work_items:read", "projects:read", "work_items:read"),
    )
    running = pending.start(at=NOW + timedelta(seconds=1))
    result = {"items": [{"id": "WI-1"}]}
    succeeded = running.succeed(at=NOW + timedelta(seconds=2), result=result)

    arguments["query"] = "changed"
    result["items"] = []

    assert pending.arguments == {"query": "overdue", "filters": ("open",)}
    assert pending.authorized_scopes == ("projects:read", "work_items:read")
    assert running.status is ToolInvocationStatus.RUNNING
    assert succeeded.status is ToolInvocationStatus.SUCCEEDED
    assert succeeded.result == {"items": ({"id": "WI-1"},)}


def test_running_invocation_can_fail_with_only_a_safe_code() -> None:
    failed = (
        pending_invocation()
        .start(at=NOW)
        .fail(
            at=NOW + timedelta(seconds=1),
            failure_code="tool_timeout",
        )
    )

    assert failed.status is ToolInvocationStatus.FAILED
    assert failed.failure_code == "tool_timeout"
    assert failed.result is None


def test_running_invocation_can_preserve_unknown_outcome() -> None:
    unknown = (
        pending_invocation()
        .start(at=NOW)
        .mark_unknown(
            at=NOW + timedelta(seconds=1),
            failure_code="tool_timeout",
        )
    )

    assert unknown.status is ToolInvocationStatus.UNKNOWN
    assert unknown.failure_code == "tool_timeout"
    assert unknown.result is None


def test_pending_invocation_can_pause_and_resume_dispatch() -> None:
    paused = pending_invocation().await_confirmation()

    assert paused.status is ToolInvocationStatus.AWAITING_CONFIRMATION
    assert paused.started_at is None
    assert paused.start(at=NOW).status is ToolInvocationStatus.RUNNING


def test_paused_invocation_can_be_cancelled_without_starting() -> None:
    cancelled = pending_invocation().await_confirmation().cancel(at=NOW)

    assert cancelled.status is ToolInvocationStatus.CANCELLED
    assert cancelled.started_at is None
    assert cancelled.completed_at == NOW


def test_invocation_number_supports_positive_multi_tool_order() -> None:
    assert replace(pending_invocation(), invocation_number=3).invocation_number == 3


@pytest.mark.parametrize(
    ("invalid_invocation", "message"),
    [
        (lambda: replace(pending_invocation(), invocation_number=0), "greater than zero"),
        (
            lambda: replace(pending_invocation(), idempotency_key="contains whitespace"),
            "idempotency_key",
        ),
        (lambda: replace(pending_invocation(), authorized_scopes=()), "must not be empty"),
        (
            lambda: replace(pending_invocation(), authorized_scopes=("INVALID SCOPE",)),
            "authorized_scopes",
        ),
        (
            lambda: replace(pending_invocation(), status=ToolInvocationStatus.RUNNING),
            "requires started_at only",
        ),
        (
            lambda: replace(
                pending_invocation(),
                status=ToolInvocationStatus.SUCCEEDED,
                started_at=NOW,
                completed_at=NOW,
            ),
            "requires result",
        ),
    ],
)
def test_invocation_rejects_invalid_identity_and_lifecycle_fields(
    invalid_invocation: Callable[[], ToolInvocation],
    message: str,
) -> None:
    with pytest.raises(DomainValidationError, match=message):
        invalid_invocation()


def test_terminal_invocation_cannot_transition_again() -> None:
    succeeded = pending_invocation().start(at=NOW).succeed(at=NOW, result={})

    with pytest.raises(InvalidStateTransition, match="cannot fail"):
        succeeded.fail(at=NOW, failure_code="late_failure")
