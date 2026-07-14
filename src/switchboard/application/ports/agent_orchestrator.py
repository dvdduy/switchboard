"""Framework-independent orchestration contracts."""

from dataclasses import dataclass
from typing import Protocol

from switchboard.application.ports.model_gateway import (
    MAX_MODEL_RESPONSE_CHARS,
    CallTool,
    ModelRequest,
    ModelToolResult,
)
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import ApprovalRequestId, ToolInvocationId

MAX_ORCHESTRATION_STEPS = 8


@dataclass(frozen=True, slots=True)
class OrchestrationRequest:
    """Input to one bounded ephemeral orchestration run."""

    initial_model_request: ModelRequest
    max_steps: int = 4

    def __post_init__(self) -> None:
        if self.initial_model_request.tool_result is not None:
            raise DomainValidationError("orchestration must start with an initial model request")
        if not 1 <= self.max_steps <= MAX_ORCHESTRATION_STEPS:
            raise DomainValidationError(
                f"max_steps must be between 1 and {MAX_ORCHESTRATION_STEPS}"
            )


@dataclass(frozen=True, slots=True)
class ToolCallAwaitingApproval:
    """Durable pause produced instead of tool output or an exception."""

    approval_id: ApprovalRequestId
    invocation_id: ToolInvocationId
    event_sequence: int

    def __post_init__(self) -> None:
        if self.event_sequence <= 0:
            raise DomainValidationError("approval event_sequence must be positive")


@dataclass(frozen=True, slots=True)
class OrchestrationResult:
    """Normalized final response or durable confirmation pause."""

    response_text: str | None
    tool_called: bool
    approval_required: ToolCallAwaitingApproval | None = None

    def __post_init__(self) -> None:
        if (self.response_text is None) == (self.approval_required is None):
            raise DomainValidationError(
                "orchestration requires exactly one response or approval pause"
            )
        if self.response_text is not None:
            if not self.response_text.strip():
                raise DomainValidationError("response_text must not be blank")
            if len(self.response_text) > MAX_MODEL_RESPONSE_CHARS:
                raise DomainValidationError("orchestration response is too long")
        elif not self.tool_called:
            raise DomainValidationError("approval pause requires a tool call")


class ToolCallHandler(Protocol):
    """Durably validate and execute one model-requested tool call."""

    async def execute(
        self,
        action: CallTool,
    ) -> ModelToolResult | ToolCallAwaitingApproval:
        """Return normalized tool data or one durable approval pause."""


class AgentOrchestrator(Protocol):
    """Coordinate one bounded run without owning durable platform state."""

    async def run(
        self,
        request: OrchestrationRequest,
        *,
        tool_handler: ToolCallHandler,
    ) -> OrchestrationResult:
        """Return final response text or raise a stable application error."""
