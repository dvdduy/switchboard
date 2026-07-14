"""PostgreSQL integration tests for conversations and durable turns."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from switchboard.adapters.persistence.schema import messages
from switchboard.adapters.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
)
from switchboard.application.errors import (
    ConversationTeamMismatchError,
    IdempotencyConflictError,
)
from switchboard.application.services.command_idempotency import hash_idempotency_key
from switchboard.application.use_cases.continue_conversation import (
    ContinueConversation,
    ContinueConversationCommand,
)
from switchboard.application.use_cases.start_conversation import (
    StartConversation,
    StartConversationCommand,
    StartConversationResult,
)
from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.command_receipts import CREATE_CONVERSATION_SCOPE, CommandOperation
from switchboard.domain.context import ContextPolicy
from switchboard.domain.conversations import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentVersionId,
    CommandReceiptId,
    ConversationId,
    MessageId,
    TeamId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.turns import Turn, TurnStatus


@dataclass(frozen=True, slots=True)
class FixedClock:
    """Clock returning one deterministic instant."""

    value: datetime

    def now(self) -> datetime:
        return self.value


@dataclass(frozen=True, slots=True)
class FixedIdGenerator[IdentifierT]:
    """Identifier generator returning one deterministic identity."""

    value: IdentifierT

    def new(self) -> IdentifierT:
        return self.value


@dataclass(frozen=True, slots=True)
class StartIds:
    conversation_id: ConversationId
    message_id: MessageId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    receipt_id: CommandReceiptId


@dataclass(frozen=True, slots=True)
class ContinueIds:
    message_id: MessageId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    receipt_id: CommandReceiptId


def new_start_ids() -> StartIds:
    return StartIds(
        conversation_id=ConversationId(uuid4()),
        message_id=MessageId(uuid4()),
        turn_id=TurnId(uuid4()),
        attempt_id=TurnAttemptId(uuid4()),
        receipt_id=CommandReceiptId(uuid4()),
    )


def new_continue_ids() -> ContinueIds:
    return ContinueIds(
        message_id=MessageId(uuid4()),
        turn_id=TurnId(uuid4()),
        attempt_id=TurnAttemptId(uuid4()),
        receipt_id=CommandReceiptId(uuid4()),
    )


async def seed_agent(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    team_id: TeamId,
    created_at: datetime,
) -> AgentVersion:
    definition = AgentDefinition(
        id=AgentDefinitionId(uuid4()),
        team_id=team_id,
        name="Project Assistant",
        created_at=created_at,
    )

    version = AgentVersion(
        id=AgentVersionId(uuid4()),
        agent_definition_id=definition.id,
        version_number=1,
        context_policy=ContextPolicy(4096, 512, 256, 256, 1),
        created_at=created_at,
    )

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.agents.add_definition(definition)
        await unit_of_work.agents.add_version(version)
        await unit_of_work.commit()

    return version


def build_start_conversation(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    now: datetime,
    ids: StartIds,
) -> StartConversation:
    return StartConversation(
        unit_of_work_factory=unit_of_work_factory,
        clock=FixedClock(now),
        conversation_ids=FixedIdGenerator(ids.conversation_id),
        message_ids=FixedIdGenerator(ids.message_id),
        turn_ids=FixedIdGenerator(ids.turn_id),
        attempt_ids=FixedIdGenerator(ids.attempt_id),
        receipt_ids=FixedIdGenerator(ids.receipt_id),
    )


def build_continue_conversation(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    now: datetime,
    ids: ContinueIds,
) -> ContinueConversation:
    return ContinueConversation(
        unit_of_work_factory=unit_of_work_factory,
        clock=FixedClock(now),
        message_ids=FixedIdGenerator(ids.message_id),
        turn_ids=FixedIdGenerator(ids.turn_id),
        attempt_ids=FixedIdGenerator(ids.attempt_id),
        receipt_ids=FixedIdGenerator(ids.receipt_id),
    )


async def start_valid_conversation(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    team_id: TeamId,
    agent_version: AgentVersion,
    now: datetime,
    ids: StartIds,
    content: str = "Show my overdue tasks.",
) -> StartConversationResult:
    use_case = build_start_conversation(
        unit_of_work_factory,
        now=now,
        ids=ids,
    )

    return await use_case.execute(
        StartConversationCommand(
            team_id=team_id,
            agent_version_id=agent_version.id,
            initial_user_message=content,
            idempotency_key=f"start-{ids.receipt_id}",
        )
    )


async def test_start_conversation_persists_complete_graph(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    team_id = TeamId(uuid4())
    ids = new_start_ids()

    agent_version = await seed_agent(
        unit_of_work_factory,
        team_id=team_id,
        created_at=now,
    )

    result = await start_valid_conversation(
        unit_of_work_factory,
        team_id=team_id,
        agent_version=agent_version,
        now=now,
        ids=ids,
    )

    # A new unit of work proves that the data survived commit and is not
    # merely present in the original SQLAlchemy session.
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(result.conversation_id)
        persisted_messages = await unit_of_work.conversations.list_messages(result.conversation_id)
        turn = await unit_of_work.turns.get(result.turn_id)
        attempts = await unit_of_work.turns.list_attempts(result.turn_id)

    assert conversation is not None
    assert conversation.team_id == team_id
    assert conversation.default_agent_version_id == agent_version.id
    assert conversation.next_message_sequence == 2

    assert len(persisted_messages) == 1
    assert persisted_messages[0].id == result.message_id
    assert persisted_messages[0].sequence == 1
    assert persisted_messages[0].role is MessageRole.USER

    assert turn is not None
    assert turn.input_message_id == result.message_id
    assert turn.agent_version_id == agent_version.id
    assert turn.status is TurnStatus.RECEIVED

    assert len(attempts) == 1
    assert attempts[0].id == result.attempt_id
    assert attempts[0].attempt_number == 1


async def test_invalid_initial_message_rolls_back_every_record(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    team_id = TeamId(uuid4())
    ids = new_start_ids()

    agent_version = await seed_agent(
        unit_of_work_factory,
        team_id=team_id,
        created_at=now,
    )

    use_case = build_start_conversation(
        unit_of_work_factory,
        now=now,
        ids=ids,
    )

    with pytest.raises(
        DomainValidationError,
        match="content must not be blank",
    ):
        await use_case.execute(
            StartConversationCommand(
                team_id=team_id,
                agent_version_id=agent_version.id,
                initial_user_message="   ",
                idempotency_key=f"start-{ids.receipt_id}",
            )
        )

    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(ids.conversation_id)
        turn = await unit_of_work.turns.get(ids.turn_id)
        receipt = await unit_of_work.command_receipts.get_by_authority(
            team_id=team_id,
            operation=CommandOperation.CREATE_CONVERSATION,
            command_scope=CREATE_CONVERSATION_SCOPE,
            idempotency_key_hash=hash_idempotency_key(f"start-{ids.receipt_id}"),
        )

    assert conversation is None
    assert turn is None
    assert receipt is None


async def test_concurrent_appends_receive_distinct_ordered_sequences(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    append_time = now + timedelta(seconds=1)
    team_id = TeamId(uuid4())
    ids = new_start_ids()

    agent_version = await seed_agent(
        unit_of_work_factory,
        team_id=team_id,
        created_at=now,
    )

    await start_valid_conversation(
        unit_of_work_factory,
        team_id=team_id,
        agent_version=agent_version,
        now=now,
        ids=ids,
    )

    async def append_message(
        *,
        message_id: MessageId,
        content: str,
    ) -> Message:
        # Each concurrent task receives a separate unit of work and
        # therefore a separate AsyncSession and transaction.
        async with unit_of_work_factory() as unit_of_work:
            message = await unit_of_work.conversations.append_message(
                conversation_id=ids.conversation_id,
                message_id=message_id,
                role=MessageRole.ASSISTANT,
                content=content,
                created_at=append_time,
            )
            await unit_of_work.commit()
            return message

    first, second = await asyncio.gather(
        append_message(
            message_id=MessageId(uuid4()),
            content="First concurrent response.",
        ),
        append_message(
            message_id=MessageId(uuid4()),
            content="Second concurrent response.",
        ),
    )

    assert {first.sequence, second.sequence} == {2, 3}

    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(ids.conversation_id)
        persisted_messages = await unit_of_work.conversations.list_messages(ids.conversation_id)

    assert conversation is not None
    assert conversation.next_message_sequence == 4
    assert [message.sequence for message in persisted_messages] == [1, 2, 3]


async def test_database_rejects_duplicate_message_sequence(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    database_engine: AsyncEngine,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    team_id = TeamId(uuid4())
    ids = new_start_ids()

    agent_version = await seed_agent(
        unit_of_work_factory,
        team_id=team_id,
        created_at=now,
    )

    await start_valid_conversation(
        unit_of_work_factory,
        team_id=team_id,
        agent_version=agent_version,
        now=now,
        ids=ids,
    )

    with pytest.raises(IntegrityError):
        async with database_engine.begin() as connection:
            await connection.execute(
                insert(messages).values(
                    id=MessageId(uuid4()),
                    conversation_id=ids.conversation_id,
                    sequence=1,
                    role=MessageRole.ASSISTANT.value,
                    content="Illegal duplicate sequence.",
                    created_at=now + timedelta(seconds=1),
                )
            )

    async with unit_of_work_factory() as unit_of_work:
        persisted_messages = await unit_of_work.conversations.list_messages(ids.conversation_id)

    assert len(persisted_messages) == 1
    assert persisted_messages[0].sequence == 1


async def test_database_rejects_second_turn_for_same_input_message(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    team_id = TeamId(uuid4())
    ids = new_start_ids()

    agent_version = await seed_agent(
        unit_of_work_factory,
        team_id=team_id,
        created_at=now,
    )

    result = await start_valid_conversation(
        unit_of_work_factory,
        team_id=team_id,
        agent_version=agent_version,
        now=now,
        ids=ids,
    )

    duplicate_turn_id = TurnId(uuid4())

    duplicate_turn = Turn(
        id=duplicate_turn_id,
        conversation_id=result.conversation_id,
        input_message_id=result.message_id,
        agent_version_id=agent_version.id,
        status=TurnStatus.RECEIVED,
        created_at=now,
    )

    with pytest.raises(IntegrityError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.turns.add(duplicate_turn)
            await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        original = await unit_of_work.turns.get(result.turn_id)
        duplicate = await unit_of_work.turns.get(duplicate_turn_id)

    assert original is not None
    assert duplicate is None


async def test_database_rejects_input_message_from_another_conversation(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    team_id = TeamId(uuid4())

    agent_version = await seed_agent(
        unit_of_work_factory,
        team_id=team_id,
        created_at=now,
    )

    first_conversation = Conversation(
        id=ConversationId(uuid4()),
        team_id=team_id,
        default_agent_version_id=agent_version.id,
        status=ConversationStatus.ACTIVE,
        next_message_sequence=1,
        created_at=now,
        updated_at=now,
    )

    second_conversation = Conversation(
        id=ConversationId(uuid4()),
        team_id=team_id,
        default_agent_version_id=agent_version.id,
        status=ConversationStatus.ACTIVE,
        next_message_sequence=1,
        created_at=now,
        updated_at=now,
    )

    mismatched_turn_id = TurnId(uuid4())

    with pytest.raises(IntegrityError):
        async with unit_of_work_factory() as unit_of_work:
            await unit_of_work.conversations.add(first_conversation)
            await unit_of_work.conversations.add(second_conversation)

            first_message = await unit_of_work.conversations.append_message(
                conversation_id=first_conversation.id,
                message_id=MessageId(uuid4()),
                role=MessageRole.USER,
                content="Message belonging to conversation one.",
                created_at=now,
            )

            mismatched_turn = Turn(
                id=mismatched_turn_id,
                conversation_id=second_conversation.id,
                input_message_id=first_message.id,
                agent_version_id=agent_version.id,
                status=TurnStatus.RECEIVED,
                created_at=now,
            )

            await unit_of_work.turns.add(mismatched_turn)
            await unit_of_work.commit()

    # The failed composite foreign key rolls back both conversations,
    # their message, and the invalid turn.
    async with unit_of_work_factory() as unit_of_work:
        first = await unit_of_work.conversations.get(first_conversation.id)
        second = await unit_of_work.conversations.get(second_conversation.id)
        mismatched = await unit_of_work.turns.get(mismatched_turn_id)

    assert first is None
    assert second is None
    assert mismatched is None


async def test_concurrent_duplicate_starts_return_one_durable_graph(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 14, 19, 0, tzinfo=UTC)
    team_id = TeamId(uuid4())
    agent_version = await seed_agent(
        unit_of_work_factory,
        team_id=team_id,
        created_at=now,
    )
    use_cases = tuple(
        build_start_conversation(
            unit_of_work_factory,
            now=now,
            ids=new_start_ids(),
        )
        for _ in range(2)
    )
    command = StartConversationCommand(
        team_id=team_id,
        agent_version_id=agent_version.id,
        initial_user_message="One accepted command",
        idempotency_key="concurrent-start",
    )

    results = await asyncio.gather(*(use_case.execute(command) for use_case in use_cases))

    assert results[0] == results[1]
    async with unit_of_work_factory() as unit_of_work:
        messages_for_result = await unit_of_work.conversations.list_messages(
            results[0].conversation_id
        )
        attempts = await unit_of_work.turns.list_attempts(results[0].turn_id)
    assert len(messages_for_result) == 1
    assert len(attempts) == 1


async def test_continue_persists_one_graph_then_replays_and_rejects_conflict(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 14, 19, 0, tzinfo=UTC)
    team_id = TeamId(uuid4())
    agent_version = await seed_agent(
        unit_of_work_factory,
        team_id=team_id,
        created_at=now,
    )
    initial = await start_valid_conversation(
        unit_of_work_factory,
        team_id=team_id,
        agent_version=agent_version,
        now=now,
        ids=new_start_ids(),
    )
    use_case = build_continue_conversation(
        unit_of_work_factory,
        now=now + timedelta(seconds=1),
        ids=new_continue_ids(),
    )
    continue_command = ContinueConversationCommand(
        team_id=team_id,
        conversation_id=initial.conversation_id,
        user_message="Continue once",
        idempotency_key="continue-replay",
    )

    first = await use_case.execute(continue_command)
    replay = await use_case.execute(continue_command)
    with pytest.raises(IdempotencyConflictError):
        await use_case.execute(
            ContinueConversationCommand(
                team_id=team_id,
                conversation_id=initial.conversation_id,
                user_message="Different content",
                idempotency_key="continue-replay",
            )
        )

    assert replay == first
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(initial.conversation_id)
        persisted_messages = await unit_of_work.conversations.list_messages(initial.conversation_id)
        turn = await unit_of_work.turns.get(first.turn_id)
        attempts = await unit_of_work.turns.list_attempts(first.turn_id)
    assert conversation is not None and conversation.next_message_sequence == 3
    assert [message.sequence for message in persisted_messages] == [1, 2]
    assert turn is not None and turn.agent_version_id == agent_version.id
    assert len(attempts) == 1


async def test_concurrent_distinct_continuations_allocate_ordered_sequences(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 14, 19, 0, tzinfo=UTC)
    team_id = TeamId(uuid4())
    agent_version = await seed_agent(
        unit_of_work_factory,
        team_id=team_id,
        created_at=now,
    )
    initial = await start_valid_conversation(
        unit_of_work_factory,
        team_id=team_id,
        agent_version=agent_version,
        now=now,
        ids=new_start_ids(),
    )
    use_cases = tuple(
        build_continue_conversation(
            unit_of_work_factory,
            now=now + timedelta(seconds=1),
            ids=new_continue_ids(),
        )
        for _ in range(2)
    )
    commands = tuple(
        ContinueConversationCommand(
            team_id=team_id,
            conversation_id=initial.conversation_id,
            user_message=f"Concurrent message {index}",
            idempotency_key=f"continue-{index}",
        )
        for index in range(2)
    )

    results = await asyncio.gather(
        *(use_case.execute(command) for use_case, command in zip(use_cases, commands, strict=True))
    )

    assert results[0].turn_id != results[1].turn_id
    async with unit_of_work_factory() as unit_of_work:
        persisted_messages = await unit_of_work.conversations.list_messages(initial.conversation_id)
    assert [message.sequence for message in persisted_messages] == [1, 2, 3]
    assert {message.content for message in persisted_messages[1:]} == {
        "Concurrent message 0",
        "Concurrent message 1",
    }


async def test_cross_team_continuation_rolls_back_its_receipt(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 14, 19, 0, tzinfo=UTC)
    owner_team_id = TeamId(uuid4())
    requesting_team_id = TeamId(uuid4())
    agent_version = await seed_agent(
        unit_of_work_factory,
        team_id=owner_team_id,
        created_at=now,
    )
    initial = await start_valid_conversation(
        unit_of_work_factory,
        team_id=owner_team_id,
        agent_version=agent_version,
        now=now,
        ids=new_start_ids(),
    )
    use_case = build_continue_conversation(
        unit_of_work_factory,
        now=now + timedelta(seconds=1),
        ids=new_continue_ids(),
    )
    key = "cross-team-continue"

    with pytest.raises(ConversationTeamMismatchError):
        await use_case.execute(
            ContinueConversationCommand(
                team_id=requesting_team_id,
                conversation_id=initial.conversation_id,
                user_message="Do not disclose",
                idempotency_key=key,
            )
        )

    async with unit_of_work_factory() as unit_of_work:
        receipt = await unit_of_work.command_receipts.get_by_authority(
            team_id=requesting_team_id,
            operation=CommandOperation.CONTINUE_CONVERSATION,
            command_scope=str(initial.conversation_id),
            idempotency_key_hash=hash_idempotency_key(key),
        )
        persisted_messages = await unit_of_work.conversations.list_messages(initial.conversation_id)
    assert receipt is None
    assert len(persisted_messages) == 1
