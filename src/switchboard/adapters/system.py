"""System-provided clock and identifier adapters."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4


class SystemClock:
    """Return the current timezone-aware UTC time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidGenerator[IdentifierT]:
    """Create strongly typed identifiers from random UUID values."""

    def __init__(self, constructor: Callable[[UUID], IdentifierT]) -> None:
        self._constructor = constructor

    def new(self) -> IdentifierT:
        return self._constructor(uuid4())
