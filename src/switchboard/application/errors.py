"""Application-level errors."""


class ApplicationError(Exception):
    """Base class for application workflow failures."""


class ConversationNotFoundError(ApplicationError):
    """Raised when an operation requires a missing conversation."""


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
