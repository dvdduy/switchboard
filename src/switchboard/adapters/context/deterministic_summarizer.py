"""Deterministic local summarizer for development and tests."""

from switchboard.application.errors import ContextBudgetExceededError
from switchboard.application.ports.token_counter import TokenCounter
from switchboard.domain.context import ContextItemCandidate, ContextItemKind
from switchboard.domain.conversations import Message


class DeterministicPrefixSummarizer:
    """Render and truncate an extractive conversation-prefix summary."""

    @property
    def version(self) -> str:
        return "deterministic-prefix-v1"

    def __init__(self, token_counter: TokenCounter) -> None:
        self._token_counter = token_counter

    async def summarize(
        self,
        *,
        messages: tuple[Message, ...],
        max_tokens: int,
    ) -> str:
        if not messages:
            raise ValueError("messages must not be empty")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be greater than zero")

        rendered = "\n".join(
            f"{message.sequence}:{message.role.value}:{message.content}" for message in messages
        )
        smallest_count: int | None = None

        for end in range(len(rendered), 0, -1):
            content = rendered[:end].rstrip()
            if not content:
                continue
            count = self._token_counter.count(
                ContextItemCandidate(
                    kind=ContextItemKind.SUMMARY,
                    role=None,
                    content=content,
                )
            )
            smallest_count = count if smallest_count is None else min(smallest_count, count)
            if count <= max_tokens:
                return content

        raise ContextBudgetExceededError(
            available_tokens=max_tokens,
            required_tokens=smallest_count or 1,
        )
