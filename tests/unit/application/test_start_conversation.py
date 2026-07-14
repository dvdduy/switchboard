from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import uuid4

import pytest

from switchboard.application.errors import (
    AgentTeamMismatchError,
    AgentVersionNotFoundError,
)
from switchboard.application.use_cases.start_conversation import (
    StartConversation,
    StartConversationCommand,
)
from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.context import ContextPolicy
from switchboard.domain.conversations import (
    Conversation,
    Message,
    MessageRole,
)
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentVersionId,
    ConversationId,
    MessageId,
    TeamId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.turns import Turn, TurnAttempt


@dataclass
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


@dataclass
class FixedIdGenerator[IdentifierT]:
    value: IdentifierT

    def new(self) -> IdentifierT:
        return self.value


class FakeAgentRepository:
    def __init__(
        self,
        *,
        definitions: tuple[AgentDefinition, ...] = (),
        versions: tuple[AgentVersion, ...] = (),
    ) -> None:
        self.definitions = {definition.id: definition for definition in definitions}
        self.versions = {version.id: version for version in versions}

    async def add_definition(
        self,
        definition: AgentDefinition,
    ) -> None:
        self.definitions[definition.id] = definition

    async def add_version(
        self,
        version: AgentVersion,
    ) -> None:
        self.versions[version.id] = version

    async def get_definition(
        self,
        agent_definition_id: AgentDefinitionId,
    ) -> AgentDefinition | None:
        return self.definitions.get(agent_definition_id)

    async def get_version(
        self,
        agent_version_id: AgentVersionId,
    ) -> AgentVersion | None:
        return self.versions.get(agent_version_id)


class FakeConversationRepository:
    def __init__(self) -> None:
        self.conversations: dict[ConversationId, Conversation] = {}
        self.messages: list[Message] = []

    async def add(self, conversation: Conversation) -> None:
        self.conversations[conversation.id] = conversation

    async def get(
        self,
        conversation_id: ConversationId,
    ) -> Conversation | None:
        return self.conversations.get(conversation_id)

    async def append_message(
        self,
        *,
        conversation_id: ConversationId,
        message_id: MessageId,
        role: MessageRole,
        content: str,
        created_at: datetime,
    ) -> Message:
        conversation = self.conversations[conversation_id]

        updated, sequence = conversation.allocate_message_sequence(at=created_at)

        message = Message(
            id=message_id,
            conversation_id=conversation_id,
            sequence=sequence,
            role=role,
            content=content,
            created_at=created_at,
        )

        self.conversations[conversation_id] = updated
        self.messages.append(message)

        return message

    async def list_messages(
        self,
        conversation_id: ConversationId,
    ) -> tuple[Message, ...]:
        return tuple(
            sorted(
                (
                    message
                    for message in self.messages
                    if message.conversation_id == conversation_id
                ),
                key=lambda message: message.sequence,
            )
        )

    def clear_created_state(self) -> None:
        self.conversations.clear()
        self.messages.clear()


class FakeTurnRepository:
    def __init__(self) -> None:
        self.turns: dict[TurnId, Turn] = {}
        self.attempts: list[TurnAttempt] = []

    async def add(self, turn: Turn) -> None:
        self.turns[turn.id] = turn

    async def get(self, turn_id: TurnId) -> Turn | None:
        return self.turns.get(turn_id)

    async def add_attempt(
        self,
        attempt: TurnAttempt,
    ) -> None:
        self.attempts.append(attempt)

    async def list_attempts(
        self,
        turn_id: TurnId,
    ) -> tuple[TurnAttempt, ...]:
        return tuple(
            sorted(
                (attempt for attempt in self.attempts if attempt.turn_id == turn_id),
                key=lambda attempt: attempt.attempt_number,
            )
        )

    def clear_created_state(self) -> None:
        self.turns.clear()
        self.attempts.clear()


class FakeUnitOfWork:
    def __init__(
        self,
        agents: FakeAgentRepository,
    ) -> None:
        self.agents = agents
        self.conversations = FakeConversationRepository()
        self.turns = FakeTurnRepository()

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
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True
        self.conversations.clear_created_state()
        self.turns.clear_created_state()


class FakeUnitOfWorkFactory:
    def __init__(
        self,
        agents: FakeAgentRepository,
    ) -> None:
        self._agents = agents
        self.latest: FakeUnitOfWork | None = None

    def __call__(self) -> FakeUnitOfWork:
        unit_of_work = FakeUnitOfWork(self._agents)
        self.latest = unit_of_work
        return unit_of_work


@dataclass(frozen=True)
class StartConversationContext:
    now: datetime
    team_id: TeamId
    conversation_id: ConversationId
    message_id: MessageId
    turn_id: TurnId
    attempt_id: TurnAttemptId
    agent_definition: AgentDefinition
    agent_version: AgentVersion


def make_context() -> StartConversationContext:
    now = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)
    team_id = TeamId(uuid4())
    agent_definition_id = AgentDefinitionId(uuid4())

    return StartConversationContext(
        now=now,
        team_id=team_id,
        conversation_id=ConversationId(uuid4()),
        message_id=MessageId(uuid4()),
        turn_id=TurnId(uuid4()),
        attempt_id=TurnAttemptId(uuid4()),
        agent_definition=AgentDefinition(
            id=agent_definition_id,
            team_id=team_id,
            name="Project Assistant",
            created_at=now,
        ),
        agent_version=AgentVersion(
            id=AgentVersionId(uuid4()),
            agent_definition_id=agent_definition_id,
            version_number=1,
            context_policy=ContextPolicy(4096, 512, 256, 256, 1),
            created_at=now,
        ),
    )


def build_use_case(
    context: StartConversationContext,
    factory: FakeUnitOfWorkFactory,
) -> StartConversation:
    return StartConversation(
        unit_of_work_factory=factory,
        clock=FixedClock(context.now),
        conversation_ids=FixedIdGenerator(context.conversation_id),
        message_ids=FixedIdGenerator(context.message_id),
        turn_ids=FixedIdGenerator(context.turn_id),
        attempt_ids=FixedIdGenerator(context.attempt_id),
    )


def require_latest(
    factory: FakeUnitOfWorkFactory,
) -> FakeUnitOfWork:
    assert factory.latest is not None
    return factory.latest


async def test_start_conversation_creates_first_turn_atomically() -> None:
    context = make_context()

    agents = FakeAgentRepository(
        definitions=(context.agent_definition,),
        versions=(context.agent_version,),
    )
    factory = FakeUnitOfWorkFactory(agents)
    use_case = build_use_case(context, factory)

    result = await use_case.execute(
        StartConversationCommand(
            team_id=context.team_id,
            agent_version_id=context.agent_version.id,
            initial_user_message="Show my overdue tasks.",
        )
    )

    unit_of_work = require_latest(factory)

    assert unit_of_work.committed is True
    assert unit_of_work.rolled_back is False

    assert result.conversation_id == context.conversation_id
    assert result.message_id == context.message_id
    assert result.turn_id == context.turn_id
    assert result.attempt_id == context.attempt_id

    conversation = unit_of_work.conversations.conversations[context.conversation_id]
    assert conversation.next_message_sequence == 2
    assert conversation.default_agent_version_id == context.agent_version.id

    assert len(unit_of_work.conversations.messages) == 1
    message = unit_of_work.conversations.messages[0]
    assert message.sequence == 1
    assert message.role is MessageRole.USER

    turn = unit_of_work.turns.turns[context.turn_id]
    assert turn.input_message_id == context.message_id
    assert turn.agent_version_id == context.agent_version.id

    assert len(unit_of_work.turns.attempts) == 1
    assert unit_of_work.turns.attempts[0].attempt_number == 1


async def test_missing_agent_version_creates_no_state() -> None:
    context = make_context()
    factory = FakeUnitOfWorkFactory(FakeAgentRepository())
    use_case = build_use_case(context, factory)

    with pytest.raises(
        AgentVersionNotFoundError,
        match="was not found",
    ):
        await use_case.execute(
            StartConversationCommand(
                team_id=context.team_id,
                agent_version_id=context.agent_version.id,
                initial_user_message="Hello",
            )
        )

    unit_of_work = require_latest(factory)

    assert unit_of_work.committed is False
    assert unit_of_work.rolled_back is True
    assert unit_of_work.conversations.conversations == {}
    assert unit_of_work.turns.turns == {}


async def test_agent_from_another_team_is_rejected() -> None:
    context = make_context()

    other_team_definition = AgentDefinition(
        id=context.agent_definition.id,
        team_id=TeamId(uuid4()),
        name=context.agent_definition.name,
        created_at=context.now,
    )

    agents = FakeAgentRepository(
        definitions=(other_team_definition,),
        versions=(context.agent_version,),
    )
    factory = FakeUnitOfWorkFactory(agents)
    use_case = build_use_case(context, factory)

    with pytest.raises(
        AgentTeamMismatchError,
        match="does not belong",
    ):
        await use_case.execute(
            StartConversationCommand(
                team_id=context.team_id,
                agent_version_id=context.agent_version.id,
                initial_user_message="Hello",
            )
        )

    unit_of_work = require_latest(factory)

    assert unit_of_work.committed is False
    assert unit_of_work.rolled_back is True
    assert unit_of_work.conversations.conversations == {}


async def test_invalid_message_rolls_back_created_conversation() -> None:
    context = make_context()

    agents = FakeAgentRepository(
        definitions=(context.agent_definition,),
        versions=(context.agent_version,),
    )
    factory = FakeUnitOfWorkFactory(agents)
    use_case = build_use_case(context, factory)

    with pytest.raises(
        DomainValidationError,
        match="content must not be blank",
    ):
        await use_case.execute(
            StartConversationCommand(
                team_id=context.team_id,
                agent_version_id=context.agent_version.id,
                initial_user_message="   ",
            )
        )

    unit_of_work = require_latest(factory)

    assert unit_of_work.committed is False
    assert unit_of_work.rolled_back is True
    assert unit_of_work.conversations.conversations == {}
    assert unit_of_work.conversations.messages == []
    assert unit_of_work.turns.turns == {}
    assert unit_of_work.turns.attempts == []
