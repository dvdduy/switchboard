"""Application-level errors."""


class ApplicationError(Exception):
    """Base class for application workflow failures."""


class ModelGatewayError(ApplicationError):
    """Base class for safe normalized model-boundary failures."""


class MalformedModelOutputError(ModelGatewayError):
    """Raised when provider output cannot become one supported action."""

    def __init__(self) -> None:
        super().__init__("model output did not match the structured action contract")


class ModelGatewayUnavailableError(ModelGatewayError):
    """Raised when the configured model gateway cannot produce an action."""

    def __init__(self) -> None:
        super().__init__("model gateway is unavailable")


class OrchestrationStepLimitError(ApplicationError):
    """Raised when an orchestration run exceeds its bounded step count."""

    def __init__(self) -> None:
        super().__init__("orchestration step limit exceeded")


class InvalidIdempotencyKeyError(ApplicationError):
    """Raised when an idempotency key violates the public command contract."""


class IdempotencyConflictError(ApplicationError):
    """Raised when an idempotency authority is reused for different content."""


class ConversationNotFoundError(ApplicationError):
    """Raised when an operation requires a missing conversation."""


class ConversationTeamMismatchError(ApplicationError):
    """Raised when a conversation does not belong to the requesting team."""


class ConversationClosedError(ApplicationError):
    """Raised when a command tries to append to a closed conversation."""


class PaginationValidationError(ApplicationError):
    """Raised when a public read cursor or page size is outside its bounds."""


class MessageNotFoundError(ApplicationError):
    """Raised when an operation requires a missing conversation message."""


class AgentVersionNotFoundError(ApplicationError):
    """Raised when a requested agent version does not exist."""


class AgentDefinitionNotFoundError(ApplicationError):
    """Raised when an agent version references a missing definition."""


class AgentTeamMismatchError(ApplicationError):
    """Raised when an agent does not belong to the requesting team."""


class TurnNotFoundError(ApplicationError):
    """Raised when an operation requires a missing turn."""


class TurnTeamMismatchError(ApplicationError):
    """Raised when a turn does not belong to the requesting team."""


class TurnAttemptNotFoundError(ApplicationError):
    """Raised when an operation requires a missing turn attempt."""


class TurnLifecycleConflictError(ApplicationError):
    """Raised when a turn changed after it was read."""


class TurnAttemptLifecycleConflictError(ApplicationError):
    """Raised when a turn attempt changed after it was read."""


class TurnAttemptMismatchError(ApplicationError):
    """Raised when an attempt does not belong to the requested turn."""


class TurnEventStateError(ApplicationError):
    """Raised when an event is incompatible with the turn lifecycle."""


class ContextBudgetExceededError(ApplicationError):
    """Raised when mandatory context cannot fit its declared input budget."""

    def __init__(
        self,
        *,
        available_tokens: int,
        required_tokens: int,
    ) -> None:
        self.available_tokens = available_tokens
        self.required_tokens = required_tokens
        super().__init__(
            f"mandatory context requires {required_tokens} tokens "
            f"but only {available_tokens} are available"
        )


class ToolDefinitionNotFoundError(ApplicationError):
    """Raised when an operation requires a missing tool definition."""


class ToolDefinitionAlreadyExistsError(ApplicationError):
    """Raised when one team already owns a requested stable tool key."""


class ToolVersionNotFoundError(ApplicationError):
    """Raised when an operation requires a missing tool version."""


class ToolTeamMismatchError(ApplicationError):
    """Raised when a tool does not belong to the requesting team."""


class ToolVersionStateError(ApplicationError):
    """Raised when a tool operation is incompatible with lifecycle state."""


class ToolConformanceRunNotFoundError(ApplicationError):
    """Raised when activation references a missing conformance run."""


class ToolConformanceFailedError(ApplicationError):
    """Raised when a failed or mismatched run is used for activation."""


class ToolAlreadyBoundError(ApplicationError):
    """Raised when an agent version already binds the stable tool identity."""


class ToolVersionLifecycleConflictError(ApplicationError):
    """Raised when tool lifecycle state changed after it was read."""


class ToolInvocationLifecycleConflictError(ApplicationError):
    """Raised when an invocation lifecycle changed after it was read."""


class WorkflowLifecycleConflictError(ApplicationError):
    """Raised when a workflow lifecycle changed after it was read."""


class WorkflowStepLifecycleConflictError(ApplicationError):
    """Raised when a workflow step lifecycle changed after it was read."""


class WorkflowPlanApprovalLifecycleConflictError(ApplicationError):
    """Raised when a workflow plan approval lifecycle changed after it was read."""


class WorkflowDiscoveryConflictError(ApplicationError):
    """Raised when a discovery command differs from persisted workflow intent."""


class WorkflowDiscoveryInProgressError(ApplicationError):
    """Raised when another runner already crossed the discovery dispatch boundary."""


class WorkflowPlanningConflictError(ApplicationError):
    """Raised when a workflow is no longer eligible for one plan freeze."""


class WorkflowPlanValidationError(ApplicationError):
    """Raised when committed discovery data cannot form a trusted bounded plan."""


class WorkflowPlanApprovalNotFoundError(ApplicationError):
    """Raised when a workflow-plan approval is unavailable to the requesting team."""


class WorkflowPlanApprovalConflictError(ApplicationError):
    """Raised when a workflow-plan approval cannot accept the requested decision."""


class WorkflowExecutionConflictError(ApplicationError):
    """Raised when persisted workflow state cannot be resumed safely."""


class WorkflowExecutionInProgressError(ApplicationError):
    """Raised when another runner already owns the next dispatch boundary."""


class WorkflowToolIneligibleError(ApplicationError):
    """Raised when a frozen mutation is no longer dispatch-eligible."""


class ApprovalLifecycleConflictError(ApplicationError):
    """Raised when an approval lifecycle changed after it was read."""


class ApprovalNotFoundError(ApplicationError):
    """Raised when an approval is unavailable to the requesting team."""


class ApprovalTeamMismatchError(ApplicationError):
    """Raised when an approval belongs to another team."""


class ApprovalDecisionConflictError(ApplicationError):
    """Raised when a decision conflicts with durable approval state."""


class ApprovalRevalidationError(ApplicationError):
    """Raised when an approved action no longer matches trusted durable state."""


class ToolDispatchError(ApplicationError):
    """Raised with a stable safe code when one tool call cannot complete."""

    def __init__(self, failure_code: str) -> None:
        self.failure_code = failure_code
        super().__init__(f"tool dispatch failed: {failure_code}")
