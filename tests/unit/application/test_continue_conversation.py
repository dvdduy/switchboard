from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import uuid4

import pytest

from switchboard.application.errors import (
    ConversationClosedError,
    ConversationNotFoundError,
    ConversationTeamMismatchError,
    IdempotencyConflictError,
)
from switchboard.application.use_cases.continue_conversation import (
    ContinueConversation,
    ContinueConversationCommand,
)
from switchboard.domain.command_receipts import CommandReceipt
from switchboard.domain.conversations import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    AgentVersionId,
    CommandReceiptId,
    ConversationId,
    MessageId,
    TeamId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.turns import Turn, TurnAttempt

NOW = datetime(2026, 7, 14, 19, 0, tzinfo=UTC)


@dataclass(frozen=True)
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


@dataclass(frozen=True)
class FixedIdGenerator[IdentifierT]:
    value: IdentifierT

    def new(self) -> IdentifierT:
        return self.value


@dataclass
class FakeState:
    conversations: dict[ConversationId, Conversation]
    messages: list[Message]
    turns: dict[TurnId, Turn]
    attempts: list[TurnAttempt]
    receipts: dict[tuple[object, ...], CommandReceipt]

    def clone(self) -> "FakeState":
        return FakeState(
            conversations=dict(self.conversations),
            messages=list(self.messages),
            turns=dict(self.turns),
            attempts=list(self.attempts),
            receipts=dict(self.receipts),
        )


class FakeCommandReceiptRepository:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def add_or_get(self, receipt: CommandReceipt) -> tuple[CommandReceipt, bool]:
        key = (
            receipt.team_id,
            receipt.operation,
            receipt.command_scope,
            receipt.idempotency_key_hash,
        )
        authority = self._state.receipts.get(key)
        if authority is not None:
            return authority, False
        self._state.receipts[key] = receipt
        return receipt, True


class FakeConversationRepository:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def get(self, conversation_id: ConversationId) -> Conversation | None:
        return self._state.conversations.get(conversation_id)

    async def append_message(
        self,
        *,
        conversation_id: ConversationId,
        message_id: MessageId,
        role: MessageRole,
        content: str,
        created_at: datetime,
    ) -> Message:
        conversation = self._state.conversations[conversation_id]
        updated, sequence = conversation.allocate_message_sequence(at=created_at)
        message = Message(
            id=message_id,
            conversation_id=conversation_id,
            sequence=sequence,
            role=role,
            content=content,
            created_at=created_at,
        )
        self._state.conversations[conversation_id] = updated
        self._state.messages.append(message)
        return message


class FakeTurnRepository:
    def __init__(self, state: FakeState) -> None:
        self._state = state

    async def add(self, turn: Turn) -> None:
        self._state.turns[turn.id] = turn

    async def add_attempt(self, attempt: TurnAttempt) -> None:
        self._state.attempts.append(attempt)


class FakeUnitOfWork:
    def __init__(self, factory: "FakeUnitOfWorkFactory") -> None:
        self._factory = factory
        self.state = factory.state.clone()
        self.command_receipts = FakeCommandReceiptRepository(self.state)
        self.conversations = FakeConversationRepository(self.state)
        self.turns = FakeTurnRepository(self.state)
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.committed:
            await self.rollback()

    async def commit(self) -> None:
        self._factory.state = self.state
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeUnitOfWorkFactory:
    def __init__(self, state: FakeState) -> None:
        self.state = state
        self.latest: FakeUnitOfWork | None = None

    def __call__(self) -> FakeUnitOfWork:
        unit_of_work = FakeUnitOfWork(self)
        self.latest = unit_of_work
        return unit_of_work


@dataclass(frozen=True)
class Context:
    team_id: TeamId
    conversation_id: ConversationId
    agent_version_id: AgentVersionId
    message_id: MessageId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    receipt_id: CommandReceiptId


def make_context() -> Context:
    return Context(
        team_id=TeamId(uuid4()),
        conversation_id=ConversationId(uuid4()),
        agent_version_id=AgentVersionId(uuid4()),
        message_id=MessageId(uuid4()),
        turn_id=TurnId(uuid4()),
        attempt_id=TurnAttemptId(uuid4()),
        receipt_id=CommandReceiptId(uuid4()),
    )


def make_factory(context: Context) -> FakeUnitOfWorkFactory:
    conversation = Conversation(
        id=context.conversation_id,
        team_id=context.team_id,
        default_agent_version_id=context.agent_version_id,
        status=ConversationStatus.ACTIVE,
        next_message_sequence=2,
        created_at=NOW,
        updated_at=NOW,
    )
    return FakeUnitOfWorkFactory(FakeState({conversation.id: conversation}, [], {}, [], {}))


def build_use_case(context: Context, factory: FakeUnitOfWorkFactory) -> ContinueConversation:
    return ContinueConversation(
        unit_of_work_factory=factory,
        clock=FixedClock(NOW),
        message_ids=FixedIdGenerator(context.message_id),
        turn_ids=FixedIdGenerator(context.turn_id),
        attempt_ids=FixedIdGenerator(context.attempt_id),
        receipt_ids=FixedIdGenerator(context.receipt_id),
    )


def command(context: Context, *, content: str = "Continue") -> ContinueConversationCommand:
    return ContinueConversationCommand(
        team_id=context.team_id,
        conversation_id=context.conversation_id,
        user_message=content,
        idempotency_key="continue-001",
    )


async def test_continue_appends_one_pinned_turn_atomically() -> None:
    context = make_context()
    factory = make_factory(context)
    result = await build_use_case(context, factory).execute(command(context))

    assert result.conversation_id == context.conversation_id
    assert factory.state.conversations[context.conversation_id].next_message_sequence == 3
    assert factory.state.messages[0].sequence == 2
    assert factory.state.messages[0].role is MessageRole.USER
    assert factory.state.turns[context.turn_id].agent_version_id == context.agent_version_id
    assert factory.state.attempts[0].attempt_number == 1
    assert len(factory.state.receipts) == 1


async def test_identical_replay_returns_original_even_after_conversation_closes() -> None:
    context = make_context()
    factory = make_factory(context)
    use_case = build_use_case(context, factory)
    first = await use_case.execute(command(context))
    conversation = factory.state.conversations[context.conversation_id]
    factory.state.conversations[context.conversation_id] = replace(
        conversation,
        status=ConversationStatus.CLOSED,
    )

    replay = await use_case.execute(command(context))

    assert replay == first
    assert len(factory.state.messages) == 1
    assert factory.latest is not None and factory.latest.rolled_back


async def test_conflicting_reuse_creates_no_second_graph() -> None:
    context = make_context()
    factory = make_factory(context)
    use_case = build_use_case(context, factory)
    await use_case.execute(command(context))

    with pytest.raises(IdempotencyConflictError):
        await use_case.execute(command(context, content="Different"))

    assert len(factory.state.messages) == 1
    assert len(factory.state.turns) == 1
    assert len(factory.state.receipts) == 1


async def test_missing_conversation_rolls_back_receipt() -> None:
    context = make_context()
    factory = make_factory(context)
    factory.state.conversations.clear()

    with pytest.raises(ConversationNotFoundError):
        await build_use_case(context, factory).execute(command(context))

    assert factory.state.receipts == {}


async def test_cross_team_conversation_rolls_back_receipt() -> None:
    context = make_context()
    factory = make_factory(context)
    foreign = factory.state.conversations[context.conversation_id]
    factory.state.conversations[context.conversation_id] = replace(
        foreign,
        team_id=TeamId(uuid4()),
    )

    with pytest.raises(ConversationTeamMismatchError):
        await build_use_case(context, factory).execute(command(context))

    assert factory.state.receipts == {}


async def test_closed_conversation_rolls_back_receipt() -> None:
    context = make_context()
    factory = make_factory(context)
    conversation = factory.state.conversations[context.conversation_id]
    factory.state.conversations[context.conversation_id] = replace(
        conversation,
        status=ConversationStatus.CLOSED,
    )

    with pytest.raises(ConversationClosedError):
        await build_use_case(context, factory).execute(command(context))

    assert factory.state.receipts == {}
    assert factory.state.messages == []


async def test_invalid_message_rolls_back_sequence_receipt_and_graph() -> None:
    context = make_context()
    factory = make_factory(context)

    with pytest.raises(DomainValidationError, match="content must not be blank"):
        await build_use_case(context, factory).execute(command(context, content="   "))

    assert factory.state.conversations[context.conversation_id].next_message_sequence == 2
    assert factory.state.receipts == {}
    assert factory.state.messages == []
    assert factory.state.turns == {}
