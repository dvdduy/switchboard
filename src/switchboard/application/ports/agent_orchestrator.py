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
class OrchestrationResult:
    """Final normalized output of a bounded graph run."""

    response_text: str
    tool_called: bool

    def __post_init__(self) -> None:
        if not self.response_text.strip():
            raise DomainValidationError("response_text must not be blank")
        if len(self.response_text) > MAX_MODEL_RESPONSE_CHARS:
            raise DomainValidationError("orchestration response is too long")


class ToolCallHandler(Protocol):
    """Durably validate and execute one model-requested tool call."""

    async def execute(self, action: CallTool) -> ModelToolResult:
        """Return normalized tool data after durable dispatch handling."""


class AgentOrchestrator(Protocol):
    """Coordinate one bounded run without owning durable platform state."""

    async def run(
        self,
        request: OrchestrationRequest,
        *,
        tool_handler: ToolCallHandler,
    ) -> OrchestrationResult:
        """Return final response text or raise a stable application error."""
