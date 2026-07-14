"""PostgreSQL coverage for bounded team-owned conversation reads."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.application.errors import (
    ConversationTeamMismatchError,
    TurnTeamMismatchError,
)
from switchboard.application.use_cases.read_conversations import (
    GetConversation,
    GetTurn,
    ListConversationMessages,
)
from switchboard.domain.conversations import MessageRole
from switchboard.domain.identifiers import MessageId, TeamId
from switchboard.domain.turns import Turn, TurnAttempt
from tests.integration.support import seed_turn

NOW = datetime(2026, 7, 14, 20, 0, tzinfo=UTC)


async def seed_history(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> tuple[TeamId, Turn, TurnAttempt]:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None

    for index in range(2, 6):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.conversations.append_message(
                conversation_id=conversation.id,
                message_id=MessageId(uuid4()),
                role=MessageRole.ASSISTANT if index % 2 == 0 else MessageRole.USER,
                content=f"Message {index}",
                created_at=NOW + timedelta(seconds=index),
            )
            await unit_of_work.commit()

    return conversation.team_id, turn, attempt


async def test_reads_conversation_and_bounded_exclusive_message_pages(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id, turn, _ = await seed_history(unit_of_work_factory)

    conversation = await GetConversation(unit_of_work_factory=unit_of_work_factory).execute(
        team_id=team_id,
        conversation_id=turn.conversation_id,
    )
    first_page = await ListConversationMessages(unit_of_work_factory=unit_of_work_factory).execute(
        team_id=team_id,
        conversation_id=turn.conversation_id,
        after_sequence=1,
        limit=2,
    )
    second_page = await ListConversationMessages(unit_of_work_factory=unit_of_work_factory).execute(
        team_id=team_id,
        conversation_id=turn.conversation_id,
        after_sequence=first_page.next_after_sequence,
        limit=2,
    )

    assert conversation.conversation_id == turn.conversation_id
    assert [message.sequence for message in first_page.items] == [2, 3]
    assert first_page.next_after_sequence == 3
    assert first_page.has_more
    assert [message.sequence for message in second_page.items] == [4, 5]
    assert second_page.next_after_sequence == 5
    assert not second_page.has_more


async def test_reads_turn_with_safe_attempt_summary(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id, turn, attempt = await seed_history(unit_of_work_factory)

    result = await GetTurn(unit_of_work_factory=unit_of_work_factory).execute(
        team_id=team_id,
        turn_id=turn.id,
    )

    assert result.turn_id == turn.id
    assert result.input_message_id == turn.input_message_id
    assert len(result.attempts) == 1
    assert result.attempts[0].attempt_number == attempt.attempt_number
    assert not hasattr(result.attempts[0], "attempt_id")
    assert not hasattr(result.attempts[0], "failure_code")


async def test_cross_team_reads_disclose_no_history_or_attempts(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    _, turn, _ = await seed_history(unit_of_work_factory)
    other_team = TeamId(uuid4())

    with pytest.raises(ConversationTeamMismatchError):
        await ListConversationMessages(unit_of_work_factory=unit_of_work_factory).execute(
            team_id=other_team,
            conversation_id=turn.conversation_id,
        )
    with pytest.raises(TurnTeamMismatchError):
        await GetTurn(unit_of_work_factory=unit_of_work_factory).execute(
            team_id=other_team,
            turn_id=turn.id,
        )
