"""Port for deterministic provider-independent context token accounting."""

from typing import Protocol

from switchboard.domain.context import ContextItemCandidate


class TokenCounter(Protocol):
    """Count complete context-item candidates under one versioned strategy."""

    @property
    def version(self) -> str:
        """Return the stable counting-strategy identifier."""

    def count(self, item: ContextItemCandidate) -> int:
        """Return the positive token count for one nonblank context item."""
