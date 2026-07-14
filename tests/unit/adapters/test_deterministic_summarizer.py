from datetime import UTC, datetime
from uuid import uuid4

import pytest

from switchboard.adapters.context.deterministic_summarizer import (
    DeterministicPrefixSummarizer,
)
from switchboard.application.errors import ContextBudgetExceededError
from switchboard.domain.context import ContextItemCandidate
from switchboard.domain.conversations import Message, MessageRole
from switchboard.domain.identifiers import ConversationId, MessageId


class CharacterTokenCounter:
    @property
    def version(self) -> str:
        return "character-v1"

    def count(self, item: ContextItemCandidate) -> int:
        return len(item.content)


class FramingHeavyTokenCounter:
    @property
    def version(self) -> str:
        return "framing-heavy-v1"

    def count(self, item: ContextItemCandidate) -> int:
        return len(item.content) + 2


def make_messages() -> tuple[Message, ...]:
    conversation_id = ConversationId(uuid4())
    now = datetime(2026, 7, 13, 22, 0, tzinfo=UTC)
    return (
        Message(
            id=MessageId(uuid4()),
            conversation_id=conversation_id,
            sequence=1,
            role=MessageRole.USER,
            content="hello",
            created_at=now,
        ),
        Message(
            id=MessageId(uuid4()),
            conversation_id=conversation_id,
            sequence=2,
            role=MessageRole.ASSISTANT,
            content="world",
            created_at=now,
        ),
    )


async def test_deterministic_summarizer_returns_bounded_extractive_prefix() -> None:
    summarizer = DeterministicPrefixSummarizer(CharacterTokenCounter())
    messages = make_messages()

    first = await summarizer.summarize(messages=messages, max_tokens=12)
    second = await summarizer.summarize(messages=messages, max_tokens=12)

    assert first == second
    assert first == "1:user:hello"
    assert len(first) <= 12
    assert summarizer.version == "deterministic-prefix-v1"


async def test_deterministic_summarizer_fails_when_no_nonblank_content_fits() -> None:
    summarizer = DeterministicPrefixSummarizer(FramingHeavyTokenCounter())

    with pytest.raises(ContextBudgetExceededError) as raised:
        await summarizer.summarize(messages=make_messages(), max_tokens=2)

    assert raised.value.available_tokens == 2
    assert raised.value.required_tokens == 3
