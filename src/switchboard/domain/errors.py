"""Domain-level errors."""


class DomainError(Exception):
    """Base class for errors caused by domain rules."""


class DomainValidationError(DomainError):
    """Raised when a domain object would be internally invalid."""


class InvalidStateTransition(DomainError):
    """Raised when an entity cannot move to the requested state."""
