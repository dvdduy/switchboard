from datetime import UTC, datetime
from random import Random
from uuid import uuid4

import pytest

from switchboard.application.errors import ContextBudgetExceededError
from switchboard.application.services.context_assembler import ContextAssembler
from switchboard.domain.context import (
    ContextItemCandidate,
    ContextPolicy,
    ConversationSummary,
    MessageContextSource,
    SummaryContextSource,
)
from switchboard.domain.conversations import Message, MessageRole
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    AgentVersionId,
    ConversationId,
    ConversationSummaryId,
    MessageId,
    TurnId,
)


class CharacterTokenCounter:
    @property
    def version(self) -> str:
        return "character-v1"

    def count(self, item: ContextItemCandidate) -> int:
        return len(item.content)


def make_policy(
    *,
    available_tokens: int = 13,
    summary_max_tokens: int = 4,
    minimum_recent_messages: int = 2,
) -> ContextPolicy:
    return ContextPolicy(
        model_window_tokens=available_tokens + 7,
        reserved_output_tokens=4,
        fixed_overhead_tokens=3,
        summary_max_tokens=summary_max_tokens,
        minimum_recent_messages=minimum_recent_messages,
    )


def make_messages(
    conversation_id: ConversationId,
    token_counts: list[int],
) -> tuple[Message, ...]:
    now = datetime(2026, 7, 13, 21, 0, tzinfo=UTC)
    return tuple(
        Message(
            id=MessageId(uuid4()),
            conversation_id=conversation_id,
            sequence=index,
            role=MessageRole.USER if index % 2 else MessageRole.ASSISTANT,
            content="x" * token_count,
            created_at=now,
        )
        for index, token_count in enumerate(token_counts, start=1)
    )


def select(
    assembler: ContextAssembler,
    *,
    messages: tuple[Message, ...],
    agent_version_id: AgentVersionId,
    policy: ContextPolicy,
):
    return assembler.select(
        conversation_id=messages[0].conversation_id,
        turn_id=TurnId(uuid4()),
        agent_version_id=agent_version_id,
        input_message_id=messages[-1].id,
        input_message_sequence=messages[-1].sequence,
        policy=policy,
        messages=messages,
    )


def make_summary(
    *,
    conversation_id: ConversationId,
    agent_version_id: AgentVersionId,
    through_sequence: int,
    token_count: int = 4,
) -> ConversationSummary:
    return ConversationSummary(
        id=ConversationSummaryId(uuid4()),
        conversation_id=conversation_id,
        agent_version_id=agent_version_id,
        from_sequence=1,
        through_sequence=through_sequence,
        content="s" * token_count,
        estimated_token_count=token_count,
        summarizer_version="prefix-v1",
        token_counter_version="character-v1",
        created_at=datetime(2026, 7, 13, 21, 1, tzinfo=UTC),
    )


def test_short_context_keeps_every_message_without_summary() -> None:
    assembler = ContextAssembler(CharacterTokenCounter())
    conversation_id = ConversationId(uuid4())
    agent_version_id = AgentVersionId(uuid4())
    messages = make_messages(conversation_id, [2, 3, 3])

    selection = select(
        assembler,
        messages=messages,
        agent_version_id=agent_version_id,
        policy=make_policy(),
    )
    context = assembler.build(selection=selection)

    assert selection.omitted_prefix == ()
    assert [
        item.source.sequence
        for item in context.items
        if isinstance(item.source, MessageContextSource)
    ] == [1, 2, 3]
    assert context.used_input_tokens == 8


def test_near_limit_context_does_not_summarize_when_all_messages_fit() -> None:
    assembler = ContextAssembler(CharacterTokenCounter())
    conversation_id = ConversationId(uuid4())
    messages = make_messages(conversation_id, [4, 4, 5])

    selection = select(
        assembler,
        messages=messages,
        agent_version_id=AgentVersionId(uuid4()),
        policy=make_policy(),
    )

    assert not selection.summary_required
    assert sum(item.token_count for item in selection.recent_items) == 13


def test_over_limit_context_uses_summary_and_newest_contiguous_suffix() -> None:
    assembler = ContextAssembler(CharacterTokenCounter())
    conversation_id = ConversationId(uuid4())
    agent_version_id = AgentVersionId(uuid4())
    messages = make_messages(conversation_id, [3, 3, 3, 4, 4])
    selection = select(
        assembler,
        messages=messages,
        agent_version_id=agent_version_id,
        policy=make_policy(),
    )
    summary = make_summary(
        conversation_id=conversation_id,
        agent_version_id=agent_version_id,
        through_sequence=3,
    )

    context = assembler.build(selection=selection, summary=summary)

    assert [message.sequence for message in selection.omitted_prefix] == [1, 2, 3]
    assert [
        item.source.sequence
        for item in selection.recent_items
        if isinstance(item.source, MessageContextSource)
    ] == [4, 5]
    assert isinstance(context.items[0].source, SummaryContextSource)
    assert context.items[0].source.through_sequence == 3
    assert context.used_input_tokens == 12
    assert context.used_input_tokens <= context.policy.available_input_tokens


def test_mandatory_recent_context_fails_instead_of_dropping_current_input() -> None:
    assembler = ContextAssembler(CharacterTokenCounter())
    conversation_id = ConversationId(uuid4())
    messages = make_messages(conversation_id, [1, 7, 7])

    with pytest.raises(ContextBudgetExceededError) as raised:
        select(
            assembler,
            messages=messages,
            agent_version_id=AgentVersionId(uuid4()),
            policy=make_policy(),
        )

    assert raised.value.available_tokens == 13
    assert raised.value.required_tokens == 14


def test_build_requires_summary_for_exact_omitted_prefix() -> None:
    assembler = ContextAssembler(CharacterTokenCounter())
    conversation_id = ConversationId(uuid4())
    agent_version_id = AgentVersionId(uuid4())
    messages = make_messages(conversation_id, [3, 3, 3, 4, 4])
    selection = select(
        assembler,
        messages=messages,
        agent_version_id=agent_version_id,
        policy=make_policy(),
    )

    with pytest.raises(DomainValidationError, match="summary is required"):
        assembler.build(selection=selection)

    wrong_coverage = make_summary(
        conversation_id=conversation_id,
        agent_version_id=agent_version_id,
        through_sequence=2,
    )
    with pytest.raises(DomainValidationError, match="cover exactly"):
        assembler.build(selection=selection, summary=wrong_coverage)


def test_build_rejects_summary_with_inaccurate_token_accounting() -> None:
    assembler = ContextAssembler(CharacterTokenCounter())
    conversation_id = ConversationId(uuid4())
    agent_version_id = AgentVersionId(uuid4())
    messages = make_messages(conversation_id, [5, 5, 4, 4])
    selection = select(
        assembler,
        messages=messages,
        agent_version_id=agent_version_id,
        policy=make_policy(),
    )
    summary = make_summary(
        conversation_id=conversation_id,
        agent_version_id=agent_version_id,
        through_sequence=2,
        token_count=4,
    )
    inaccurate = ConversationSummary(
        id=summary.id,
        conversation_id=summary.conversation_id,
        agent_version_id=summary.agent_version_id,
        from_sequence=summary.from_sequence,
        through_sequence=summary.through_sequence,
        content=summary.content + "s",
        estimated_token_count=summary.estimated_token_count,
        summarizer_version=summary.summarizer_version,
        token_counter_version=summary.token_counter_version,
        created_at=summary.created_at,
    )

    with pytest.raises(DomainValidationError, match="estimated token count must match"):
        assembler.build(selection=selection, summary=inaccurate)


@pytest.mark.parametrize(
    "mutation",
    ["wrong_conversation", "out_of_order", "wrong_input"],
)
def test_selection_rejects_invalid_message_snapshots(mutation: str) -> None:
    assembler = ContextAssembler(CharacterTokenCounter())
    conversation_id = ConversationId(uuid4())
    agent_version_id = AgentVersionId(uuid4())
    messages = list(make_messages(conversation_id, [2, 2]))
    input_message_id = messages[-1].id

    if mutation == "wrong_conversation":
        message = messages[0]
        messages[0] = Message(
            id=message.id,
            conversation_id=ConversationId(uuid4()),
            sequence=message.sequence,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
        )
    elif mutation == "out_of_order":
        messages.reverse()
    else:
        input_message_id = MessageId(uuid4())

    with pytest.raises(DomainValidationError):
        assembler.select(
            conversation_id=conversation_id,
            turn_id=TurnId(uuid4()),
            agent_version_id=agent_version_id,
            input_message_id=input_message_id,
            input_message_sequence=2,
            policy=make_policy(),
            messages=tuple(messages),
        )


def test_varied_histories_never_exceed_the_declared_budget() -> None:
    random = Random(20260713)
    assembler = ContextAssembler(CharacterTokenCounter())
    successful_builds = 0

    for _ in range(250):
        conversation_id = ConversationId(uuid4())
        agent_version_id = AgentVersionId(uuid4())
        message_count = random.randint(1, 12)
        token_counts = [random.randint(1, 8) for _ in range(message_count)]
        messages = make_messages(conversation_id, token_counts)
        available = random.randint(8, 30)
        summary_max = random.randint(1, available - 1)
        minimum_recent = random.randint(1, min(4, message_count))
        policy = make_policy(
            available_tokens=available,
            summary_max_tokens=summary_max,
            minimum_recent_messages=minimum_recent,
        )

        try:
            selection = select(
                assembler,
                messages=messages,
                agent_version_id=agent_version_id,
                policy=policy,
            )
        except ContextBudgetExceededError:
            continue

        summary = None
        if selection.summary_required:
            summary = make_summary(
                conversation_id=conversation_id,
                agent_version_id=agent_version_id,
                through_sequence=selection.summary_through_sequence or 0,
                token_count=summary_max,
            )
        context = assembler.build(selection=selection, summary=summary)
        successful_builds += 1

        assert context.used_input_tokens <= policy.available_input_tokens
        assert context.items[-1].source == MessageContextSource(
            message_id=messages[-1].id,
            sequence=messages[-1].sequence,
        )
        assert context.items[-1].mandatory
        recent_sequences = [
            item.source.sequence
            for item in context.items
            if isinstance(item.source, MessageContextSource)
        ]
        assert recent_sequences == list(range(recent_sequences[0], messages[-1].sequence + 1))

    assert successful_builds > 100
