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
