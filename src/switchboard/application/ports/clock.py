"""Port for obtaining the current time."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Provides the current timezone-aware UTC time."""

    def now(self) -> datetime:
        """Return the current time."""
