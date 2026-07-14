from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from switchboard.domain.errors import (
    DomainValidationError,
    InvalidStateTransition,
)
from switchboard.domain.identifiers import (
    AgentVersionId,
    ConversationId,
    MessageId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.turns import (
    Turn,
    TurnAttempt,
    TurnAttemptStatus,
    TurnStatus,
)


def make_turn() -> Turn:
    return Turn(
        id=TurnId(uuid4()),
        conversation_id=ConversationId(uuid4()),
        input_message_id=MessageId(uuid4()),
        agent_version_id=AgentVersionId(uuid4()),
        status=TurnStatus.RECEIVED,
        created_at=datetime(2026, 7, 13, 16, 0, tzinfo=UTC),
    )


def make_attempt() -> TurnAttempt:
    return TurnAttempt(
        id=TurnAttemptId(uuid4()),
        turn_id=TurnId(uuid4()),
        attempt_number=1,
        status=TurnAttemptStatus.PENDING,
        created_at=datetime(2026, 7, 13, 16, 0, tzinfo=UTC),
    )


def test_turn_follows_valid_lifecycle() -> None:
    turn = make_turn()
    running = turn.start()
    completed_at = turn.created_at + timedelta(seconds=5)
    completed = running.complete(at=completed_at)

    assert turn.status is TurnStatus.RECEIVED
    assert running.status is TurnStatus.RUNNING
    assert completed.status is TurnStatus.COMPLETED
    assert completed.completed_at == completed_at


def test_completed_turn_cannot_be_started_again() -> None:
    turn = make_turn().start().complete(at=datetime(2026, 7, 13, 16, 1, tzinfo=UTC))

    with pytest.raises(
        InvalidStateTransition,
        match="cannot start a turn",
    ):
        turn.start()


def test_terminal_turn_requires_completion_time() -> None:
    with pytest.raises(
        DomainValidationError,
        match="terminal turn status requires completed_at",
    ):
        Turn(
            id=TurnId(uuid4()),
            conversation_id=ConversationId(uuid4()),
            input_message_id=MessageId(uuid4()),
            agent_version_id=AgentVersionId(uuid4()),
            status=TurnStatus.COMPLETED,
            created_at=datetime(2026, 7, 13, tzinfo=UTC),
        )


def test_attempt_follows_valid_lifecycle() -> None:
    attempt = make_attempt()
    started_at = attempt.created_at + timedelta(seconds=1)
    completed_at = started_at + timedelta(seconds=2)

    running = attempt.start(at=started_at)
    succeeded = running.succeed(at=completed_at)

    assert attempt.status is TurnAttemptStatus.PENDING
    assert running.status is TurnAttemptStatus.RUNNING
    assert running.started_at == started_at
    assert succeeded.status is TurnAttemptStatus.SUCCEEDED
    assert succeeded.completed_at == completed_at


def test_failed_attempt_requires_failure_code() -> None:
    started_at = datetime(2026, 7, 13, 16, 1, tzinfo=UTC)
    completed_at = started_at + timedelta(seconds=1)

    with pytest.raises(
        DomainValidationError,
        match="failed attempt requires failure_code",
    ):
        TurnAttempt(
            id=TurnAttemptId(uuid4()),
            turn_id=TurnId(uuid4()),
            attempt_number=1,
            status=TurnAttemptStatus.FAILED,
            created_at=started_at,
            started_at=started_at,
            completed_at=completed_at,
        )


def test_attempt_number_must_be_positive() -> None:
    with pytest.raises(
        DomainValidationError,
        match="attempt_number must be greater than zero",
    ):
        TurnAttempt(
            id=TurnAttemptId(uuid4()),
            turn_id=TurnId(uuid4()),
            attempt_number=0,
            status=TurnAttemptStatus.PENDING,
            created_at=datetime(2026, 7, 13, tzinfo=UTC),
        )


def test_turn_allocates_event_sequences_without_mutating_original() -> None:
    turn = make_turn()

    after_first, first_sequence = turn.allocate_event_sequence()
    after_second, second_sequence = after_first.allocate_event_sequence()

    assert first_sequence == 1
    assert second_sequence == 2

    assert turn.next_event_sequence == 1
    assert after_first.next_event_sequence == 2
    assert after_second.next_event_sequence == 3


def test_turn_next_event_sequence_must_be_positive() -> None:
    with pytest.raises(
        DomainValidationError,
        match=("next_event_sequence must be greater than zero"),
    ):
        Turn(
            id=TurnId(uuid4()),
            conversation_id=ConversationId(uuid4()),
            input_message_id=MessageId(uuid4()),
            agent_version_id=AgentVersionId(uuid4()),
            status=TurnStatus.RECEIVED,
            created_at=datetime(
                2026,
                7,
                13,
                tzinfo=UTC,
            ),
            next_event_sequence=0,
        )
