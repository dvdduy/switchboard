from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from switchboard.application.errors import ContextBudgetExceededError
from switchboard.application.ports.token_counter import TokenCounter
from switchboard.domain.context import (
    BuiltContext,
    ContextItem,
    ContextItemCandidate,
    ContextItemKind,
    ContextPolicy,
    ConversationSummary,
    MessageContextSource,
    SummaryContextSource,
)
from switchboard.domain.conversations import MessageRole
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    AgentVersionId,
    ConversationId,
    ConversationSummaryId,
    MessageId,
    TurnId,
)


def make_policy() -> ContextPolicy:
    return ContextPolicy(
        model_window_tokens=4096,
        reserved_output_tokens=512,
        fixed_overhead_tokens=256,
        summary_max_tokens=256,
        minimum_recent_messages=1,
    )


def make_summary(**overrides: object) -> ConversationSummary:
    values: dict[str, object] = {
        "id": ConversationSummaryId(uuid4()),
        "conversation_id": ConversationId(uuid4()),
        "agent_version_id": AgentVersionId(uuid4()),
        "from_sequence": 1,
        "through_sequence": 3,
        "content": "Earlier requirements and decisions.",
        "estimated_token_count": 5,
        "summarizer_version": "prefix-v1",
        "token_counter_version": "word-count-v1",
        "created_at": datetime(2026, 7, 13, tzinfo=UTC),
    }
    values.update(overrides)
    return ConversationSummary(**values)  # type: ignore[arg-type]


def test_conversation_summary_exposes_complete_provenance() -> None:
    summary = make_summary()

    assert summary.source == SummaryContextSource(
        agent_version_id=summary.agent_version_id,
        from_sequence=1,
        through_sequence=3,
        summarizer_version="prefix-v1",
        token_counter_version="word-count-v1",
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"from_sequence": 2}, "coverage must start at sequence 1"),
        ({"through_sequence": 0}, "through_sequence must be greater than zero"),
        ({"content": "  "}, "content must not be blank"),
        ({"estimated_token_count": 0}, "estimated_token_count must be greater than zero"),
    ],
)
def test_conversation_summary_rejects_invalid_artifacts(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(DomainValidationError, match=message):
        make_summary(**overrides)


def make_message_item(
    *,
    message_id: MessageId | None = None,
    sequence: int = 1,
    token_count: int = 3,
    mandatory: bool = True,
) -> ContextItem:
    return ContextItem(
        candidate=ContextItemCandidate(
            kind=ContextItemKind.MESSAGE,
            role=MessageRole.USER,
            content="Show overdue work.",
        ),
        source=MessageContextSource(
            message_id=MessageId(uuid4()) if message_id is None else message_id,
            sequence=sequence,
        ),
        token_count=token_count,
        mandatory=mandatory,
    )


def test_context_policy_calculates_available_input_budget() -> None:
    policy = make_policy()

    assert policy.available_input_tokens == 3328


@pytest.mark.parametrize(
    "field_name",
    [
        "model_window_tokens",
        "reserved_output_tokens",
        "fixed_overhead_tokens",
        "summary_max_tokens",
        "minimum_recent_messages",
    ],
)
def test_context_policy_requires_positive_fields(field_name: str) -> None:
    with pytest.raises(
        DomainValidationError,
        match=f"{field_name} must be greater than zero",
    ):
        replace(make_policy(), **{field_name: 0})


def test_context_policy_must_leave_conversation_capacity() -> None:
    with pytest.raises(
        DomainValidationError,
        match="must leave conversation input capacity",
    ):
        ContextPolicy(
            model_window_tokens=100,
            reserved_output_tokens=60,
            fixed_overhead_tokens=40,
            summary_max_tokens=10,
            minimum_recent_messages=1,
        )


def test_summary_reservation_must_be_smaller_than_input_budget() -> None:
    with pytest.raises(
        DomainValidationError,
        match="summary_max_tokens must be smaller",
    ):
        ContextPolicy(
            model_window_tokens=100,
            reserved_output_tokens=20,
            fixed_overhead_tokens=20,
            summary_max_tokens=60,
            minimum_recent_messages=1,
        )


def test_context_item_candidate_preserves_message_content() -> None:
    candidate = ContextItemCandidate(
        kind=ContextItemKind.MESSAGE,
        role=MessageRole.USER,
        content="  preserve spacing  ",
    )

    assert candidate.content == "  preserve spacing  "


@pytest.mark.parametrize(
    ("kind", "role", "message"),
    [
        (ContextItemKind.MESSAGE, None, "message context item must have a role"),
        (
            ContextItemKind.SUMMARY,
            MessageRole.ASSISTANT,
            "summary context item must not have a message role",
        ),
    ],
)
def test_context_item_candidate_requires_kind_appropriate_role(
    kind: ContextItemKind,
    role: MessageRole | None,
    message: str,
) -> None:
    with pytest.raises(DomainValidationError, match=message):
        ContextItemCandidate(
            kind=kind,
            role=role,
            content="Context",
        )


def test_summary_source_normalizes_version_provenance() -> None:
    source = SummaryContextSource(
        agent_version_id=AgentVersionId(uuid4()),
        from_sequence=1,
        through_sequence=3,
        summarizer_version="  deterministic-v1  ",
        token_counter_version="  word-count-v1  ",
    )

    assert source.summarizer_version == "deterministic-v1"
    assert source.token_counter_version == "word-count-v1"


def test_summary_source_requires_prefix_coverage() -> None:
    with pytest.raises(
        DomainValidationError,
        match="summary coverage must start at sequence 1",
    ):
        SummaryContextSource(
            agent_version_id=AgentVersionId(uuid4()),
            from_sequence=2,
            through_sequence=3,
            summarizer_version="deterministic-v1",
            token_counter_version="word-count-v1",
        )


def test_counted_context_item_must_match_source_kind() -> None:
    with pytest.raises(
        DomainValidationError,
        match="summary context item must reference a summary source",
    ):
        ContextItem(
            candidate=ContextItemCandidate(
                kind=ContextItemKind.SUMMARY,
                role=None,
                content="Older context.",
            ),
            source=MessageContextSource(
                message_id=MessageId(uuid4()),
                sequence=1,
            ),
            token_count=2,
        )


def test_built_context_reports_accounting_and_cutoff_provenance() -> None:
    input_message_id = MessageId(uuid4())
    item = make_message_item(message_id=input_message_id)

    context = BuiltContext(
        conversation_id=ConversationId(uuid4()),
        turn_id=TurnId(uuid4()),
        agent_version_id=AgentVersionId(uuid4()),
        input_message_id=input_message_id,
        input_message_sequence=1,
        policy=make_policy(),
        token_counter_version="word-count-v1",
        items=(item,),
    )

    assert context.items == (item,)
    assert context.used_input_tokens == 3
    assert context.remaining_input_tokens == 3325


def test_built_context_rejects_output_over_budget() -> None:
    input_message_id = MessageId(uuid4())

    with pytest.raises(
        DomainValidationError,
        match="built context exceeds available input tokens",
    ):
        BuiltContext(
            conversation_id=ConversationId(uuid4()),
            turn_id=TurnId(uuid4()),
            agent_version_id=AgentVersionId(uuid4()),
            input_message_id=input_message_id,
            input_message_sequence=1,
            policy=make_policy(),
            token_counter_version="word-count-v1",
            items=(
                make_message_item(
                    message_id=input_message_id,
                    token_count=3329,
                ),
            ),
        )


def test_built_context_rejects_messages_after_input_cutoff() -> None:
    input_message_id = MessageId(uuid4())

    with pytest.raises(
        DomainValidationError,
        match="must not exceed the input-message cutoff",
    ):
        BuiltContext(
            conversation_id=ConversationId(uuid4()),
            turn_id=TurnId(uuid4()),
            agent_version_id=AgentVersionId(uuid4()),
            input_message_id=input_message_id,
            input_message_sequence=2,
            policy=make_policy(),
            token_counter_version="word-count-v1",
            items=(
                make_message_item(
                    message_id=input_message_id,
                    sequence=2,
                ),
                make_message_item(sequence=3, mandatory=False),
            ),
        )


def test_built_context_requires_current_input_once_and_mandatory() -> None:
    input_message_id = MessageId(uuid4())

    with pytest.raises(
        DomainValidationError,
        match="current input context item must be mandatory",
    ):
        BuiltContext(
            conversation_id=ConversationId(uuid4()),
            turn_id=TurnId(uuid4()),
            agent_version_id=AgentVersionId(uuid4()),
            input_message_id=input_message_id,
            input_message_sequence=1,
            policy=make_policy(),
            token_counter_version="word-count-v1",
            items=(
                make_message_item(
                    message_id=input_message_id,
                    mandatory=False,
                ),
            ),
        )


class WordTokenCounter:
    @property
    def version(self) -> str:
        return "word-count-v1"

    def count(self, item: ContextItemCandidate) -> int:
        return len(item.content.split()) + (1 if item.role is not None else 0)


def count_candidate(
    counter: TokenCounter,
    candidate: ContextItemCandidate,
) -> int:
    return counter.count(candidate)


def test_token_counter_port_accepts_versioned_deterministic_counter() -> None:
    counter = WordTokenCounter()
    candidate = ContextItemCandidate(
        kind=ContextItemKind.MESSAGE,
        role=MessageRole.USER,
        content="Show overdue work",
    )

    assert counter.version == "word-count-v1"
    assert count_candidate(counter, candidate) == 4


def test_context_budget_error_exposes_structured_accounting() -> None:
    error = ContextBudgetExceededError(
        available_tokens=13,
        required_tokens=14,
    )

    assert error.available_tokens == 13
    assert error.required_tokens == 14
    assert str(error) == ("mandatory context requires 14 tokens but only 13 are available")
