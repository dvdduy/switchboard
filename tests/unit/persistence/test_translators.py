from datetime import UTC, datetime, timedelta
from uuid import uuid4

from switchboard.adapters.persistence.translators import (
    agent_definition_from_record,
    agent_definition_to_record,
    agent_version_from_record,
    agent_version_to_record,
    conversation_from_record,
    conversation_summary_from_record,
    conversation_summary_to_record,
    conversation_to_record,
    execution_event_from_record,
    execution_event_to_record,
    message_from_record,
    message_to_record,
    turn_attempt_from_record,
    turn_attempt_to_record,
    turn_from_record,
    turn_to_record,
)
from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.context import ContextPolicy, ConversationSummary
from switchboard.domain.conversations import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from switchboard.domain.execution_events import (
    ExecutionEvent,
    ExecutionEventKind,
)
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentVersionId,
    ConversationId,
    ConversationSummaryId,
    ExecutionEventId,
    MessageId,
    TeamId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.turns import (
    Turn,
    TurnAttempt,
    TurnAttemptStatus,
    TurnStatus,
)


def test_agent_definition_round_trip() -> None:
    definition = AgentDefinition(
        id=AgentDefinitionId(uuid4()),
        team_id=TeamId(uuid4()),
        name="Project Assistant",
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
    )

    assert agent_definition_from_record(agent_definition_to_record(definition)) == definition


def test_agent_version_round_trip() -> None:
    version = AgentVersion(
        id=AgentVersionId(uuid4()),
        agent_definition_id=AgentDefinitionId(uuid4()),
        version_number=1,
        context_policy=ContextPolicy(4096, 512, 256, 256, 1),
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
    )

    assert agent_version_from_record(agent_version_to_record(version)) == version


def test_conversation_round_trip() -> None:
    conversation = Conversation(
        id=ConversationId(uuid4()),
        team_id=TeamId(uuid4()),
        default_agent_version_id=AgentVersionId(uuid4()),
        status=ConversationStatus.ACTIVE,
        next_message_sequence=3,
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
        updated_at=datetime(2026, 7, 13, 0, 1, tzinfo=UTC),
    )

    assert conversation_from_record(conversation_to_record(conversation)) == conversation


def test_conversation_summary_round_trip() -> None:
    summary = ConversationSummary(
        id=ConversationSummaryId(uuid4()),
        conversation_id=ConversationId(uuid4()),
        agent_version_id=AgentVersionId(uuid4()),
        from_sequence=1,
        through_sequence=4,
        content="Earlier requirements and decisions.",
        estimated_token_count=5,
        summarizer_version="prefix-v1",
        token_counter_version="word-count-v1",
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
    )

    assert conversation_summary_from_record(conversation_summary_to_record(summary)) == summary


def test_message_round_trip() -> None:
    message = Message(
        id=MessageId(uuid4()),
        conversation_id=ConversationId(uuid4()),
        sequence=1,
        role=MessageRole.USER,
        content="Show my overdue work.",
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
    )

    assert message_from_record(message_to_record(message)) == message


def test_turn_round_trip() -> None:
    created_at = datetime(2026, 7, 13, tzinfo=UTC)

    turn = Turn(
        id=TurnId(uuid4()),
        conversation_id=ConversationId(uuid4()),
        input_message_id=MessageId(uuid4()),
        agent_version_id=AgentVersionId(uuid4()),
        status=TurnStatus.COMPLETED,
        created_at=created_at,
        completed_at=created_at + timedelta(seconds=5),
    )

    assert turn_from_record(turn_to_record(turn)) == turn


def test_turn_attempt_round_trip() -> None:
    created_at = datetime(2026, 7, 13, tzinfo=UTC)
    started_at = created_at + timedelta(seconds=1)

    attempt = TurnAttempt(
        id=TurnAttemptId(uuid4()),
        turn_id=TurnId(uuid4()),
        attempt_number=1,
        status=TurnAttemptStatus.FAILED,
        created_at=created_at,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
        failure_code="dependency_unavailable",
    )

    assert turn_attempt_from_record(turn_attempt_to_record(attempt)) == attempt


def test_execution_event_round_trip() -> None:
    event = ExecutionEvent(
        id=ExecutionEventId(uuid4()),
        turn_id=TurnId(uuid4()),
        attempt_id=TurnAttemptId(uuid4()),
        sequence=3,
        kind=ExecutionEventKind.RESPONSE_DELTA,
        payload={
            "text": "Project ",
            "metadata": {
                "indexes": [1, 2],
            },
        },
        occurred_at=datetime(
            2026,
            7,
            13,
            tzinfo=UTC,
        ),
    )

    restored = execution_event_from_record(execution_event_to_record(event))

    assert restored == event
    assert restored.payload["metadata"] == {
        "indexes": (1, 2),
    }
