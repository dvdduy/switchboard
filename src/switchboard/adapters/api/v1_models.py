"""Versioned transport models for the public conversation API."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from switchboard.domain.approvals import ApprovalStatus
from switchboard.domain.command_receipts import ApprovalDecision
from switchboard.domain.conversations import ConversationStatus, MessageRole
from switchboard.domain.tool_invocations import ToolInvocationStatus
from switchboard.domain.tools import ToolEffect
from switchboard.domain.turns import TurnAttemptStatus, TurnStatus
from switchboard.domain.workflows import WorkflowStatus

MAX_MESSAGE_CONTENT_LENGTH = 32_000
MessageContent = Annotated[
    str,
    Field(min_length=1, max_length=MAX_MESSAGE_CONTENT_LENGTH),
]


class V1Model(BaseModel):
    """Strict base for stable v1 transport contracts."""

    model_config = ConfigDict(extra="forbid")


class V1CreateConversationRequest(V1Model):
    agent_version_id: UUID
    initial_user_message: MessageContent

    @field_validator("initial_user_message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class V1ContinueConversationRequest(V1Model):
    user_message: MessageContent

    @field_validator("user_message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class V1AcceptedTurnResponse(V1Model):
    conversation_id: UUID
    message_id: UUID
    turn_id: UUID
    status: TurnStatus
    conversation_url: str
    events_url: str


class V1ConversationResponse(V1Model):
    conversation_id: UUID
    default_agent_version_id: UUID
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime


class V1MessageResponse(V1Model):
    message_id: UUID
    sequence: int
    role: MessageRole
    content: str
    created_at: datetime


class V1MessagePageResponse(V1Model):
    items: tuple[V1MessageResponse, ...]
    next_after_sequence: int
    has_more: bool


class V1TurnAttemptResponse(V1Model):
    attempt_number: int
    status: TurnAttemptStatus
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class V1TurnResponse(V1Model):
    turn_id: UUID
    conversation_id: UUID
    input_message_id: UUID
    agent_version_id: UUID
    status: TurnStatus
    created_at: datetime
    completed_at: datetime | None
    attempts: tuple[V1TurnAttemptResponse, ...]
    events_url: str


class V1ApprovalDecisionRequest(V1Model):
    decision: ApprovalDecision


class V1ApprovalSafeSummary(V1Model):
    tool_definition_id: UUID
    tool_version_id: UUID
    effect: ToolEffect
    argument_fields: tuple[str, ...]


class V1ApprovalTargetType(StrEnum):
    INVOCATION = "invocation"
    WORKFLOW_PLAN = "workflow_plan"


class V1WorkflowApprovalSafeAction(V1ApprovalSafeSummary):
    step_number: int


class V1ApprovalResponse(V1Model):
    approval_id: UUID
    invocation_id: UUID | None
    workflow_id: UUID | None = None
    target_type: V1ApprovalTargetType = V1ApprovalTargetType.INVOCATION
    requester_actor_id: UUID
    status: ApprovalStatus
    safe_summary: V1ApprovalSafeSummary | None
    safe_actions: tuple[V1WorkflowApprovalSafeAction, ...] = ()
    mutation_count: int | None = None
    fingerprint_version: str
    created_at: datetime
    expires_at: datetime
    resolved_by_actor_id: UUID | None
    resolved_at: datetime | None


class V1ApprovalDecisionResponse(V1ApprovalResponse):
    invocation_status: ToolInvocationStatus | None = None
    workflow_status: WorkflowStatus | None = None


class V1ValidationIssue(V1Model):
    field: str
    code: str


class V1ErrorDetail(V1Model):
    code: str
    message: str
    details: tuple[V1ValidationIssue, ...] = ()


class V1ErrorResponse(V1Model):
    error: V1ErrorDetail
