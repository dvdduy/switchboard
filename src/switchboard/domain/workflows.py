"""Framework-independent durable multi-step workflow state."""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from hashlib import sha256

from switchboard.domain.common import normalize_utc, require_not_before, require_positive
from switchboard.domain.errors import DomainValidationError, InvalidStateTransition
from switchboard.domain.identifiers import (
    ActorId,
    AgentVersionId,
    MessageId,
    TeamId,
    ToolInvocationId,
    TurnAttemptId,
    TurnId,
    TurnWorkflowId,
    WorkflowPlanApprovalId,
    WorkflowStepId,
)
from switchboard.domain.json_values import canonical_json
from switchboard.domain.policy import (
    ACTION_FINGERPRINT_VERSION,
    ActionFingerprint,
    PolicyEnvironment,
)

WORKFLOW_PLAN_FINGERPRINT_VERSION = "workflow-plan-v1"
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FAILURE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,99}$")


@dataclass(frozen=True, slots=True)
class WorkflowPlanAction:
    """Stable ordered identity of one exact mutation in a frozen plan."""

    step_number: int
    invocation_id: ToolInvocationId
    fingerprint: ActionFingerprint

    def __post_init__(self) -> None:
        require_positive(self.step_number, field_name="step_number")


def fingerprint_workflow_plan(
    *,
    team_id: TeamId,
    requester_actor_id: ActorId,
    agent_version_id: AgentVersionId,
    workflow_id: TurnWorkflowId,
    plan_version: int,
    environment: PolicyEnvironment,
    policy_version: str,
    actions: tuple[WorkflowPlanAction, ...],
) -> "WorkflowPlanFingerprint":
    """Bind approval to the exact ordered mutation identities and actions."""

    require_positive(plan_version, field_name="plan_version")
    step_numbers = tuple(action.step_number for action in actions)
    if step_numbers != tuple(sorted(set(step_numbers))):
        raise DomainValidationError("workflow plan actions must have unique increasing steps")
    if any(action.fingerprint.version != ACTION_FINGERPRINT_VERSION for action in actions):
        raise DomainValidationError("workflow plan action fingerprint version is unsupported")
    envelope = {
        "actions": [
            {
                "action_fingerprint": {
                    "digest": action.fingerprint.digest,
                    "version": action.fingerprint.version,
                },
                "invocation_id": str(action.invocation_id),
                "step_number": action.step_number,
            }
            for action in actions
        ],
        "agent_version_id": str(agent_version_id),
        "environment": environment.value,
        "plan_version": plan_version,
        "policy_version": policy_version,
        "requester_actor_id": str(requester_actor_id),
        "team_id": str(team_id),
        "workflow_id": str(workflow_id),
    }
    return WorkflowPlanFingerprint(
        version=WORKFLOW_PLAN_FINGERPRINT_VERSION,
        digest=sha256(canonical_json(envelope).encode("utf-8")).hexdigest(),
    )


class WorkflowStatus(StrEnum):
    """Lifecycle of one durable turn workflow."""

    DISCOVERY_PENDING = "discovery_pending"
    DISCOVERY_RUNNING = "discovery_running"
    DISCOVERY_FAILED = "discovery_failed"
    PLANNING = "planning"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RUNNING = "running"
    COMPLETING = "completing"
    COMPLETED = "completed"
    FAILED = "failed"
    REVIEW_REQUIRED = "review_required"
    CANCELLED = "cancelled"


class WorkflowStepKind(StrEnum):
    """Bounded Day 9 workflow step types."""

    DISCOVERY_TOOL = "discovery_tool"
    MUTATION_TOOL = "mutation_tool"
    FINAL_RESPONSE = "final_response"


class WorkflowStepStatus(StrEnum):
    """Lifecycle of one ordered workflow step."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class WorkflowPlanFingerprint:
    """Internal digest binding one exact ordered mutation plan."""

    version: str
    digest: str

    def __post_init__(self) -> None:
        if self.version != WORKFLOW_PLAN_FINGERPRINT_VERSION:
            raise DomainValidationError("workflow plan fingerprint version is unsupported")
        if not _DIGEST_PATTERN.fullmatch(self.digest):
            raise DomainValidationError("workflow plan fingerprint digest is invalid")


@dataclass(frozen=True, slots=True)
class TurnWorkflow:
    """Durable business progress for one bounded logical turn."""

    id: TurnWorkflowId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    status: WorkflowStatus
    plan_version: int
    created_at: datetime
    updated_at: datetime
    plan_fingerprint: WorkflowPlanFingerprint | None = None
    approval_id: WorkflowPlanApprovalId | None = None
    output_message_id: MessageId | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        require_positive(self.plan_version, field_name="plan_version")
        if self.plan_version != 1:
            raise DomainValidationError("Day 9 supports workflow plan version 1")

        created_at = normalize_utc(self.created_at, field_name="created_at")
        updated_at = normalize_utc(self.updated_at, field_name="updated_at")
        require_not_before(
            updated_at,
            minimum=created_at,
            field_name="updated_at",
            minimum_field_name="created_at",
        )
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)

        completed_at = self.completed_at
        if completed_at is not None:
            completed_at = normalize_utc(completed_at, field_name="completed_at")
            require_not_before(
                completed_at,
                minimum=updated_at,
                field_name="completed_at",
                minimum_field_name="updated_at",
            )
            object.__setattr__(self, "completed_at", completed_at)

        before_freeze = {
            WorkflowStatus.DISCOVERY_PENDING,
            WorkflowStatus.DISCOVERY_RUNNING,
            WorkflowStatus.PLANNING,
        }
        terminal_with_output = {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.REVIEW_REQUIRED,
        }
        if self.status in before_freeze:
            if any(
                value is not None
                for value in (
                    self.plan_fingerprint,
                    self.approval_id,
                    self.output_message_id,
                    completed_at,
                )
            ):
                raise DomainValidationError("unfrozen workflow contains frozen or terminal state")
        elif self.status is WorkflowStatus.DISCOVERY_FAILED:
            if (
                self.plan_fingerprint is not None
                or self.approval_id is not None
                or self.output_message_id is not None
                or completed_at is None
            ):
                raise DomainValidationError(
                    "failed discovery requires only terminal completion time"
                )
        elif self.status in {WorkflowStatus.AWAITING_CONFIRMATION, WorkflowStatus.RUNNING}:
            if self.plan_fingerprint is None or self.approval_id is None:
                raise DomainValidationError(
                    "approved execution state requires frozen plan approval"
                )
            if self.output_message_id is not None or completed_at is not None:
                raise DomainValidationError("active workflow must not contain terminal output")
        elif self.status is WorkflowStatus.COMPLETING:
            if self.plan_fingerprint is None:
                raise DomainValidationError("completing workflow requires frozen plan")
            if self.output_message_id is not None or completed_at is not None:
                raise DomainValidationError("completing workflow must not contain terminal output")
        elif self.status in terminal_with_output:
            if (
                self.plan_fingerprint is None
                or self.output_message_id is None
                or completed_at is None
            ):
                raise DomainValidationError(
                    "terminal workflow requires frozen plan, output, and time"
                )
        elif self.status is WorkflowStatus.CANCELLED and (
            self.plan_fingerprint is None
            or self.approval_id is None
            or self.output_message_id is not None
            or completed_at is None
        ):
            raise DomainValidationError("cancelled workflow requires approval and completion time")

    def start_discovery(self, *, at: datetime) -> "TurnWorkflow":
        return self._transition(
            expected=WorkflowStatus.DISCOVERY_PENDING,
            status=WorkflowStatus.DISCOVERY_RUNNING,
            at=at,
        )

    def begin_planning(self, *, at: datetime) -> "TurnWorkflow":
        return self._transition(
            expected=WorkflowStatus.DISCOVERY_RUNNING,
            status=WorkflowStatus.PLANNING,
            at=at,
        )

    def fail_discovery(self, *, at: datetime) -> "TurnWorkflow":
        self._require_status(WorkflowStatus.DISCOVERY_RUNNING)
        completed_at = normalize_utc(at, field_name="at")
        return replace(
            self,
            status=WorkflowStatus.DISCOVERY_FAILED,
            updated_at=completed_at,
            completed_at=completed_at,
        )

    def await_confirmation(
        self,
        *,
        fingerprint: WorkflowPlanFingerprint,
        approval_id: WorkflowPlanApprovalId,
        at: datetime,
    ) -> "TurnWorkflow":
        self._require_status(WorkflowStatus.PLANNING)
        return replace(
            self,
            status=WorkflowStatus.AWAITING_CONFIRMATION,
            plan_fingerprint=fingerprint,
            approval_id=approval_id,
            updated_at=normalize_utc(at, field_name="at"),
        )

    def begin_completion_without_mutations(
        self,
        *,
        fingerprint: WorkflowPlanFingerprint,
        at: datetime,
    ) -> "TurnWorkflow":
        self._require_status(WorkflowStatus.PLANNING)
        return replace(
            self,
            status=WorkflowStatus.COMPLETING,
            plan_fingerprint=fingerprint,
            updated_at=normalize_utc(at, field_name="at"),
        )

    def resume(self, *, at: datetime) -> "TurnWorkflow":
        return self._transition(
            expected=WorkflowStatus.AWAITING_CONFIRMATION,
            status=WorkflowStatus.RUNNING,
            at=at,
        )

    def begin_completion(self, *, at: datetime) -> "TurnWorkflow":
        return self._transition(
            expected=WorkflowStatus.RUNNING,
            status=WorkflowStatus.COMPLETING,
            at=at,
        )

    def cancel(self, *, at: datetime) -> "TurnWorkflow":
        self._require_status(WorkflowStatus.AWAITING_CONFIRMATION)
        completed_at = normalize_utc(at, field_name="at")
        return replace(
            self,
            status=WorkflowStatus.CANCELLED,
            updated_at=completed_at,
            completed_at=completed_at,
        )

    def complete(
        self,
        *,
        output_message_id: MessageId,
        at: datetime,
    ) -> "TurnWorkflow":
        return self._finish(
            status=WorkflowStatus.COMPLETED,
            output_message_id=output_message_id,
            at=at,
        )

    def fail(
        self,
        *,
        output_message_id: MessageId,
        at: datetime,
    ) -> "TurnWorkflow":
        return self._finish(
            status=WorkflowStatus.FAILED,
            output_message_id=output_message_id,
            at=at,
        )

    def require_review(
        self,
        *,
        output_message_id: MessageId,
        at: datetime,
    ) -> "TurnWorkflow":
        return self._finish(
            status=WorkflowStatus.REVIEW_REQUIRED,
            output_message_id=output_message_id,
            at=at,
        )

    def _finish(
        self,
        *,
        status: WorkflowStatus,
        output_message_id: MessageId,
        at: datetime,
    ) -> "TurnWorkflow":
        self._require_status(WorkflowStatus.COMPLETING)
        completed_at = normalize_utc(at, field_name="at")
        return replace(
            self,
            status=status,
            output_message_id=output_message_id,
            updated_at=completed_at,
            completed_at=completed_at,
        )

    def _transition(
        self,
        *,
        expected: WorkflowStatus,
        status: WorkflowStatus,
        at: datetime,
    ) -> "TurnWorkflow":
        self._require_status(expected)
        return replace(
            self,
            status=status,
            updated_at=normalize_utc(at, field_name="at"),
        )

    def _require_status(self, expected: WorkflowStatus) -> None:
        if self.status is not expected:
            raise InvalidStateTransition(
                f"cannot transition workflow from {self.status.value}; expected {expected.value}"
            )


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    """One typed ordered step whose executable identity is immutable."""

    id: WorkflowStepId
    workflow_id: TurnWorkflowId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    step_number: int
    kind: WorkflowStepKind
    status: WorkflowStepStatus
    created_at: datetime
    predecessor_step_id: WorkflowStepId | None = None
    predecessor_step_number: int | None = None
    invocation_id: ToolInvocationId | None = None
    output_message_id: MessageId | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        require_positive(self.step_number, field_name="step_number")
        if self.step_number == 1:
            if self.predecessor_step_id is not None or self.predecessor_step_number is not None:
                raise DomainValidationError("first workflow step must not have a predecessor")
        elif (
            self.predecessor_step_id is None or self.predecessor_step_number != self.step_number - 1
        ):
            raise DomainValidationError("workflow step must reference its immediate predecessor")

        if self.kind in {WorkflowStepKind.DISCOVERY_TOOL, WorkflowStepKind.MUTATION_TOOL}:
            if self.invocation_id is None or self.output_message_id is not None:
                raise DomainValidationError("tool step requires only an invocation link")
        elif self.invocation_id is not None:
            raise DomainValidationError("final response step must not reference an invocation")

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
            require_not_before(
                completed_at,
                minimum=started_at or created_at,
                field_name="completed_at",
                minimum_field_name="started_at" if started_at is not None else "created_at",
            )
            object.__setattr__(self, "completed_at", completed_at)

        if self.failure_code is not None and not _FAILURE_CODE_PATTERN.fullmatch(self.failure_code):
            raise DomainValidationError("workflow step failure_code is invalid")

        if self.status is WorkflowStepStatus.PENDING:
            if any(
                value is not None
                for value in (started_at, completed_at, self.output_message_id, self.failure_code)
            ):
                raise DomainValidationError("pending workflow step contains lifecycle output")
        elif self.status is WorkflowStepStatus.RUNNING:
            if started_at is None or any(
                value is not None
                for value in (completed_at, self.output_message_id, self.failure_code)
            ):
                raise DomainValidationError("running workflow step requires only started_at")
        elif self.status is WorkflowStepStatus.SUCCEEDED:
            if started_at is None or completed_at is None or self.failure_code is not None:
                raise DomainValidationError("succeeded workflow step requires execution times")
            if self.kind is WorkflowStepKind.FINAL_RESPONSE and self.output_message_id is None:
                raise DomainValidationError("successful final response requires output message")
        elif self.status in {WorkflowStepStatus.FAILED, WorkflowStepStatus.UNKNOWN}:
            if (
                started_at is None
                or completed_at is None
                or self.failure_code is None
                or self.output_message_id is not None
            ):
                raise DomainValidationError("failed workflow step requires times and safe code")
        elif self.status is WorkflowStepStatus.SKIPPED and (
            started_at is not None
            or completed_at is None
            or self.failure_code is None
            or self.output_message_id is not None
        ):
            raise DomainValidationError("skipped workflow step requires completion and safe code")

    def start(self, *, at: datetime) -> "WorkflowStep":
        if self.status is not WorkflowStepStatus.PENDING:
            raise InvalidStateTransition(f"cannot start workflow step from {self.status.value}")
        return replace(
            self,
            status=WorkflowStepStatus.RUNNING,
            started_at=normalize_utc(at, field_name="at"),
        )

    def succeed(
        self,
        *,
        at: datetime,
        output_message_id: MessageId | None = None,
    ) -> "WorkflowStep":
        if self.status is not WorkflowStepStatus.RUNNING:
            raise InvalidStateTransition(f"cannot succeed workflow step from {self.status.value}")
        return replace(
            self,
            status=WorkflowStepStatus.SUCCEEDED,
            output_message_id=output_message_id,
            completed_at=normalize_utc(at, field_name="at"),
        )

    def fail(self, *, at: datetime, failure_code: str) -> "WorkflowStep":
        return self._finish_unsuccessfully(
            status=WorkflowStepStatus.FAILED,
            at=at,
            failure_code=failure_code,
        )

    def mark_unknown(self, *, at: datetime, failure_code: str) -> "WorkflowStep":
        return self._finish_unsuccessfully(
            status=WorkflowStepStatus.UNKNOWN,
            at=at,
            failure_code=failure_code,
        )

    def skip(self, *, at: datetime, failure_code: str) -> "WorkflowStep":
        if self.status is not WorkflowStepStatus.PENDING:
            raise InvalidStateTransition(f"cannot skip workflow step from {self.status.value}")
        return replace(
            self,
            status=WorkflowStepStatus.SKIPPED,
            completed_at=normalize_utc(at, field_name="at"),
            failure_code=failure_code,
        )

    def _finish_unsuccessfully(
        self,
        *,
        status: WorkflowStepStatus,
        at: datetime,
        failure_code: str,
    ) -> "WorkflowStep":
        if self.status is not WorkflowStepStatus.RUNNING:
            raise InvalidStateTransition(f"cannot finish workflow step from {self.status.value}")
        return replace(
            self,
            status=status,
            completed_at=normalize_utc(at, field_name="at"),
            failure_code=failure_code,
        )
