"""Application-level errors."""


class ApplicationError(Exception):
    """Base class for application workflow failures."""


class ConversationNotFoundError(ApplicationError):
    """Raised when an operation requires a missing conversation."""


class AgentVersionNotFoundError(ApplicationError):
    """Raised when a requested agent version does not exist."""


class AgentDefinitionNotFoundError(ApplicationError):
    """Raised when an agent version references a missing definition."""


class AgentTeamMismatchError(ApplicationError):
    """Raised when an agent does not belong to the requesting team."""
