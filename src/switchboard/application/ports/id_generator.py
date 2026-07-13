"""Port for generating typed entity identifiers."""

from typing import Protocol, TypeVar

IdentifierT_co = TypeVar("IdentifierT_co", covariant=True)


class IdGenerator(Protocol[IdentifierT_co]):
    """Generates a new identifier of one declared type."""

    def new(self) -> IdentifierT_co:
        """Generate a new identifier."""
