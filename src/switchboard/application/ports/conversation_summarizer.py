"""Port for deriving a bounded summary from immutable conversation messages."""

from typing import Protocol

from switchboard.domain.conversations import Message


class ConversationSummarizer(Protocol):
    """Create one versioned summary without persistence side effects."""

    @property
    def version(self) -> str:
        """Return the stable summarization-strategy identifier."""

    async def summarize(
        self,
        *,
        messages: tuple[Message, ...],
        max_tokens: int,
    ) -> str:
        """Summarize an ordered message prefix within the requested limit."""
