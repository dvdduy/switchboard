from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import uuid4

import pytest

from switchboard.application.errors import (
    ConversationNotFoundError,
    ConversationTeamMismatchError,
    PaginationValidationError,
    TurnNotFoundError,
    TurnTeamMismatchError,
)
from switchboard.application.use_cases.read_conversations import (
    GetConversation,
    GetTurn,
    ListConversationMessages,
)
from switchboard.domain.conversations import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from switchboard.domain.identifiers import (
    AgentVersionId,
    ConversationId,
    MessageId,
    TeamId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.turns import Turn, TurnAttempt, TurnAttemptStatus, TurnStatus

NOW = datetime(2026, 7, 14, 20, 0, tzinfo=UTC)


class FakeConversationRepository:
    def __init__(
        self,
        conversation: Conversation | None,
        messages: tuple[Message, ...] = (),
    ) -> None:
        self.conversation = conversation
        self.messages = messages
        self.page_queries: list[tuple[int, int]] = []

    async def get(self, conversation_id: ConversationId) -> Conversation | None:
        if self.conversation is None or self.conversation.id != conversation_id:
            return None
        return self.conversation

    async def list_messages_after(
        self,
        *,
        conversation_id: ConversationId,
        after_sequence: int,
        limit: int,
    ) -> tuple[Message, ...]:
        self.page_queries.append((after_sequence, limit))
        return tuple(
            message
            for message in sorted(self.messages, key=lambda item: item.sequence)
            if message.conversation_id == conversation_id and message.sequence > after_sequence
        )[:limit]


class FakeTurnRepository:
    def __init__(
        self,
        turn: Turn | None = None,
        attempts: tuple[TurnAttempt, ...] = (),
    ) -> None:
        self.turn = turn
        self.attempts = attempts

    async def get(self, turn_id: TurnId) -> Turn | None:
        if self.turn is None or self.turn.id != turn_id:
            return None
        return self.turn

    async def list_attempts(self, turn_id: TurnId) -> tuple[TurnAttempt, ...]:
        return tuple(
            sorted(
                (attempt for attempt in self.attempts if attempt.turn_id == turn_id),
                key=lambda attempt: attempt.attempt_number,
            )
        )


class FakeUnitOfWork:
    def __init__(
        self,
        factory: "FakeUnitOfWorkFactory",
        conversations: FakeConversationRepository,
        turns: FakeTurnRepository,
    ) -> None:
        self._factory = factory
        self.conversations = conversations
        self.turns = turns

    async def __aenter__(self) -> Self:
        self._factory.active += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._factory.active -= 1


class FakeUnitOfWorkFactory:
    def __init__(
        self,
        conversations: FakeConversationRepository,
        turns: FakeTurnRepository | None = None,
    ) -> None:
        self.conversations = conversations
        self.turns = turns or FakeTurnRepository()
        self.created = 0
        self.active = 0

    def __call__(self) -> FakeUnitOfWork:
        self.created += 1
        return FakeUnitOfWork(self, self.conversations, self.turns)


def make_conversation(*, team_id: TeamId | None = None) -> Conversation:
    return Conversation(
        id=ConversationId(uuid4()),
        team_id=team_id or TeamId(uuid4()),
        default_agent_version_id=AgentVersionId(uuid4()),
        status=ConversationStatus.ACTIVE,
        next_message_sequence=4,
        created_at=NOW,
        updated_at=NOW,
    )


def make_message(conversation: Conversation, sequence: int) -> Message:
    return Message(
        id=MessageId(uuid4()),
        conversation_id=conversation.id,
        sequence=sequence,
        role=MessageRole.USER if sequence % 2 else MessageRole.ASSISTANT,
        content=f"Message {sequence}",
        created_at=NOW + timedelta(seconds=sequence),
    )


def make_turn(conversation: Conversation) -> Turn:
    return Turn(
        id=TurnId(uuid4()),
        conversation_id=conversation.id,
        input_message_id=MessageId(uuid4()),
        agent_version_id=conversation.default_agent_version_id,
        status=TurnStatus.RUNNING,
        created_at=NOW,
    )


async def test_get_conversation_returns_safe_metadata_after_short_read() -> None:
    conversation = make_conversation()
    factory = FakeUnitOfWorkFactory(FakeConversationRepository(conversation))

    result = await GetConversation(unit_of_work_factory=factory).execute(
        team_id=conversation.team_id,
        conversation_id=conversation.id,
    )

    assert result.conversation_id == conversation.id
    assert result.default_agent_version_id == conversation.default_agent_version_id
    assert result.status is ConversationStatus.ACTIVE
    assert factory.created == 1
    assert factory.active == 0


@pytest.mark.parametrize("conversation", [None, make_conversation()])
async def test_get_conversation_rejects_missing_or_cross_team(
    conversation: Conversation | None,
) -> None:
    factory = FakeUnitOfWorkFactory(FakeConversationRepository(conversation))
    conversation_id = conversation.id if conversation is not None else ConversationId(uuid4())
    error = ConversationNotFoundError if conversation is None else ConversationTeamMismatchError

    with pytest.raises(error):
        await GetConversation(unit_of_work_factory=factory).execute(
            team_id=TeamId(uuid4()),
            conversation_id=conversation_id,
        )

    assert factory.active == 0


@pytest.mark.parametrize(
    ("after_sequence", "limit"),
    [(-1, 50), (0, 0), (0, 101)],
)
async def test_invalid_pagination_is_rejected_before_read(
    after_sequence: int,
    limit: int,
) -> None:
    conversation = make_conversation()
    factory = FakeUnitOfWorkFactory(FakeConversationRepository(conversation))

    with pytest.raises(PaginationValidationError):
        await ListConversationMessages(unit_of_work_factory=factory).execute(
            team_id=conversation.team_id,
            conversation_id=conversation.id,
            after_sequence=after_sequence,
            limit=limit,
        )

    assert factory.created == 0


async def test_message_page_uses_exclusive_cursor_and_one_row_lookahead() -> None:
    conversation = make_conversation()
    repository = FakeConversationRepository(
        conversation,
        tuple(make_message(conversation, sequence) for sequence in range(1, 5)),
    )
    factory = FakeUnitOfWorkFactory(repository)

    page = await ListConversationMessages(unit_of_work_factory=factory).execute(
        team_id=conversation.team_id,
        conversation_id=conversation.id,
        after_sequence=1,
        limit=2,
    )

    assert [item.sequence for item in page.items] == [2, 3]
    assert page.next_after_sequence == 3
    assert page.has_more
    assert repository.page_queries == [(1, 3)]
    assert factory.active == 0


async def test_empty_message_page_preserves_input_cursor() -> None:
    conversation = make_conversation()
    factory = FakeUnitOfWorkFactory(FakeConversationRepository(conversation))

    page = await ListConversationMessages(unit_of_work_factory=factory).execute(
        team_id=conversation.team_id,
        conversation_id=conversation.id,
        after_sequence=9,
        limit=10,
    )

    assert page.items == ()
    assert page.next_after_sequence == 9
    assert not page.has_more


async def test_cross_team_history_is_rejected_before_message_query() -> None:
    conversation = make_conversation()
    repository = FakeConversationRepository(conversation, (make_message(conversation, 1),))
    factory = FakeUnitOfWorkFactory(repository)

    with pytest.raises(ConversationTeamMismatchError):
        await ListConversationMessages(unit_of_work_factory=factory).execute(
            team_id=TeamId(uuid4()),
            conversation_id=conversation.id,
        )

    assert repository.page_queries == []


async def test_get_turn_returns_ordered_safe_attempt_summaries() -> None:
    conversation = make_conversation()
    turn = make_turn(conversation)
    pending = TurnAttempt(
        id=TurnAttemptId(uuid4()),
        turn_id=turn.id,
        attempt_number=2,
        status=TurnAttemptStatus.PENDING,
        created_at=NOW,
    )
    failed = TurnAttempt(
        id=TurnAttemptId(uuid4()),
        turn_id=turn.id,
        attempt_number=1,
        status=TurnAttemptStatus.FAILED,
        created_at=NOW,
        started_at=NOW,
        completed_at=NOW,
        failure_code="provider_timeout",
    )
    factory = FakeUnitOfWorkFactory(
        FakeConversationRepository(conversation),
        FakeTurnRepository(turn, (pending, failed)),
    )

    result = await GetTurn(unit_of_work_factory=factory).execute(
        team_id=conversation.team_id,
        turn_id=turn.id,
    )

    assert result.turn_id == turn.id
    assert [attempt.attempt_number for attempt in result.attempts] == [1, 2]
    assert not hasattr(result.attempts[0], "attempt_id")
    assert not hasattr(result.attempts[0], "failure_code")
    assert factory.active == 0


async def test_get_turn_rejects_missing_and_cross_team_without_attempt_disclosure() -> None:
    conversation = make_conversation()
    turn = make_turn(conversation)
    missing_factory = FakeUnitOfWorkFactory(
        FakeConversationRepository(conversation),
        FakeTurnRepository(),
    )
    with pytest.raises(TurnNotFoundError):
        await GetTurn(unit_of_work_factory=missing_factory).execute(
            team_id=conversation.team_id,
            turn_id=turn.id,
        )

    cross_team_factory = FakeUnitOfWorkFactory(
        FakeConversationRepository(conversation),
        FakeTurnRepository(turn),
    )
    with pytest.raises(TurnTeamMismatchError):
        await GetTurn(unit_of_work_factory=cross_team_factory).execute(
            team_id=TeamId(uuid4()),
            turn_id=turn.id,
        )
