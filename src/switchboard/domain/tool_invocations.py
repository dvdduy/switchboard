"""Durable logical tool invocation and lifecycle transitions."""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from switchboard.domain.common import normalize_utc, require_not_before, require_positive
from switchboard.domain.errors import DomainValidationError, InvalidStateTransition
from switchboard.domain.identifiers import (
    ToolDefinitionId,
    ToolInvocationId,
    ToolVersionId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.json_values import JsonObject, freeze_json_object

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9._:-]{0,99}$")
_FAILURE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,99}$")


class ToolInvocationStatus(StrEnum):
    """Lifecycle of one logical installed-tool invocation."""

    PENDING = "pending"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Durable exact-version invocation independent of any adapter SDK."""

    id: ToolInvocationId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    invocation_number: int
    tool_definition_id: ToolDefinitionId
    tool_version_id: ToolVersionId
    arguments: JsonObject
    idempotency_key: str
    authorized_scopes: tuple[str, ...]
    status: ToolInvocationStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: JsonObject | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        require_positive(self.invocation_number, field_name="invocation_number")
        if self.invocation_number != 1:
            raise DomainValidationError("Day 7 supports exactly one invocation per attempt")

        if not _KEY_PATTERN.fullmatch(self.idempotency_key):
            raise DomainValidationError("idempotency_key is invalid")

        scopes = tuple(sorted(set(self.authorized_scopes)))
        if not scopes:
            raise DomainValidationError("authorized_scopes must not be empty")
        if len(scopes) > 32 or any(not _SCOPE_PATTERN.fullmatch(scope) for scope in scopes):
            raise DomainValidationError("authorized_scopes is invalid")
        object.__setattr__(self, "authorized_scopes", scopes)
        object.__setattr__(
            self,
            "arguments",
            freeze_json_object(self.arguments, field_name="arguments"),
        )

        created_at = normalize_utc(self.created_at, field_name="created_at")
        object.__setattr__(self, "created_at", created_at)
        started_at = self.started_at
        completed_at = self.completed_at
        if started_at is not None:
            started_at = normalize_utc(started_at, field_name="started_at")
            require_not_before(
                started_at,
                minimum=created_at,
                field_name="started_at",
                minimum_field_name="created_at",
            )
            object.__setattr__(self, "started_at", started_at)
        if completed_at is not None:
            completed_at = normalize_utc(completed_at, field_name="completed_at")
            if started_at is None and self.status is not ToolInvocationStatus.CANCELLED:
                raise DomainValidationError("completed invocation requires started_at")
            require_not_before(
                completed_at,
                minimum=created_at,
                field_name="completed_at",
                minimum_field_name="created_at",
            )
            if started_at is not None:
                require_not_before(
                    completed_at,
                    minimum=started_at,
                    field_name="completed_at",
                    minimum_field_name="started_at",
                )
            object.__setattr__(self, "completed_at", completed_at)

        result = self.result
        if result is not None:
            result = freeze_json_object(result, field_name="result")
            object.__setattr__(self, "result", result)
        failure_code = self.failure_code
        if failure_code is not None and not _FAILURE_CODE_PATTERN.fullmatch(failure_code):
            raise DomainValidationError("failure_code is invalid")

        if self.status is ToolInvocationStatus.PENDING:
            if any(value is not None for value in (started_at, completed_at, result, failure_code)):
                raise DomainValidationError("pending invocation must not contain execution results")
        elif self.status is ToolInvocationStatus.AWAITING_CONFIRMATION:
            if any(value is not None for value in (started_at, completed_at, result, failure_code)):
                raise DomainValidationError(
                    "awaiting-confirmation invocation must not contain execution results"
                )
        elif self.status is ToolInvocationStatus.RUNNING:
            if started_at is None or any(
                value is not None for value in (completed_at, result, failure_code)
            ):
                raise DomainValidationError("running invocation requires started_at only")
        elif self.status is ToolInvocationStatus.SUCCEEDED:
            if (
                started_at is None
                or completed_at is None
                or result is None
                or failure_code is not None
            ):
                raise DomainValidationError("succeeded invocation requires result and timestamps")
        elif self.status is ToolInvocationStatus.FAILED and (
            started_at is None or completed_at is None or result is not None or failure_code is None
        ):
            raise DomainValidationError("failed invocation requires failure code and timestamps")
        elif self.status is ToolInvocationStatus.CANCELLED and (
            started_at is not None
            or completed_at is None
            or result is not None
            or failure_code is not None
        ):
            raise DomainValidationError("cancelled invocation requires only completion timestamp")

    def start(self, *, at: datetime) -> "ToolInvocation":
        if self.status not in {
            ToolInvocationStatus.PENDING,
            ToolInvocationStatus.AWAITING_CONFIRMATION,
        }:
            raise InvalidStateTransition(f"cannot start invocation from {self.status.value}")
        return replace(
            self,
            status=ToolInvocationStatus.RUNNING,
            started_at=normalize_utc(at, field_name="at"),
        )

    def await_confirmation(self) -> "ToolInvocation":
        if self.status is not ToolInvocationStatus.PENDING:
            raise InvalidStateTransition(f"cannot await confirmation from {self.status.value}")
        return replace(self, status=ToolInvocationStatus.AWAITING_CONFIRMATION)

    def succeed(self, *, at: datetime, result: JsonObject) -> "ToolInvocation":
        if self.status is not ToolInvocationStatus.RUNNING:
            raise InvalidStateTransition(f"cannot succeed invocation from {self.status.value}")
        return replace(
            self,
            status=ToolInvocationStatus.SUCCEEDED,
            completed_at=normalize_utc(at, field_name="at"),
            result=result,
        )

    def fail(self, *, at: datetime, failure_code: str) -> "ToolInvocation":
        if self.status is not ToolInvocationStatus.RUNNING:
            raise InvalidStateTransition(f"cannot fail invocation from {self.status.value}")
        return replace(
            self,
            status=ToolInvocationStatus.FAILED,
            completed_at=normalize_utc(at, field_name="at"),
            failure_code=failure_code,
        )

    def cancel(self, *, at: datetime) -> "ToolInvocation":
        """Cancel an invocation that never crossed the dispatch boundary."""

        if self.status is not ToolInvocationStatus.AWAITING_CONFIRMATION:
            raise InvalidStateTransition(f"cannot cancel invocation from {self.status.value}")
        return replace(
            self,
            status=ToolInvocationStatus.CANCELLED,
            completed_at=normalize_utc(at, field_name="at"),
        )
