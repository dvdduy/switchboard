import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Self
from uuid import uuid4

import pytest

from switchboard.application.errors import (
    AgentDefinitionNotFoundError,
    AgentTeamMismatchError,
    AgentVersionNotFoundError,
    ContextBudgetExceededError,
    ConversationNotFoundError,
    TurnNotFoundError,
)
from switchboard.application.use_cases.build_turn_context import (
    BuildTurnContext,
    BuildTurnContextCommand,
)
from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.context import (
    ContextItemCandidate,
    ContextPolicy,
    ConversationSummary,
    MessageContextSource,
    SummaryContextSource,
)
from switchboard.domain.conversations import (
    Conversation,
    ConversationStatus,
    Message,
    MessageRole,
)
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentVersionId,
    ConversationId,
    ConversationSummaryId,
    MessageId,
    TeamId,
    TurnId,
)
from switchboard.domain.turns import Turn, TurnStatus


class CharacterTokenCounter:
    @property
    def version(self) -> str:
        return "character-v1"

    def count(self, item: ContextItemCandidate) -> int:
        return len(item.content)


@dataclass(frozen=True, slots=True)
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


class SequenceIdGenerator:
    def new(self) -> ConversationSummaryId:
        return ConversationSummaryId(uuid4())


class FakeAgentRepository:
    def __init__(
        self,
        definition: AgentDefinition | None,
        version: AgentVersion | None,
    ) -> None:
        self.definition = definition
        self.version = version

    async def get_version(self, agent_version_id: AgentVersionId) -> AgentVersion | None:
        if self.version is None or self.version.id != agent_version_id:
            return None
        return self.version

    async def get_definition(
        self,
        agent_definition_id: AgentDefinitionId,
    ) -> AgentDefinition | None:
        if self.definition is None or self.definition.id != agent_definition_id:
            return None
        return self.definition


class FakeConversationRepository:
    def __init__(
        self,
        conversation: Conversation | None,
        messages: tuple[Message, ...],
    ) -> None:
        self.conversation = conversation
        self.messages = messages
        self.cutoffs: list[int] = []

    async def get(self, conversation_id: ConversationId) -> Conversation | None:
        if self.conversation is None or self.conversation.id != conversation_id:
            return None
        return self.conversation

    async def get_message(
        self,
        *,
        conversation_id: ConversationId,
        message_id: MessageId,
    ) -> Message | None:
        return next(
            (
                message
                for message in self.messages
                if message.conversation_id == conversation_id and message.id == message_id
            ),
            None,
        )

    async def list_messages_through(
        self,
        *,
        conversation_id: ConversationId,
        through_sequence: int,
    ) -> tuple[Message, ...]:
        self.cutoffs.append(through_sequence)
        return tuple(
            message
            for message in self.messages
            if message.conversation_id == conversation_id and message.sequence <= through_sequence
        )


class FakeTurnRepository:
    def __init__(self, turn: Turn | None) -> None:
        self.turn = turn

    async def get(self, turn_id: TurnId) -> Turn | None:
        if self.turn is None or self.turn.id != turn_id:
            return None
        return self.turn


class FakeSummaryRepository:
    def __init__(self, summaries: tuple[ConversationSummary, ...] = ()) -> None:
        self._summaries = list(summaries)
        self._lock = asyncio.Lock()
        self.add_calls = 0

    @property
    def summaries(self) -> tuple[ConversationSummary, ...]:
        return tuple(self._summaries)

    async def get_latest_compatible(
        self,
        *,
        conversation_id: ConversationId,
        agent_version_id: AgentVersionId,
        through_sequence: int,
        summarizer_version: str,
        token_counter_version: str,
    ) -> ConversationSummary | None:
        compatible = [
            summary
            for summary in self._summaries
            if summary.conversation_id == conversation_id
            and summary.agent_version_id == agent_version_id
            and summary.through_sequence <= through_sequence
            and summary.summarizer_version == summarizer_version
            and summary.token_counter_version == token_counter_version
        ]
        return max(compatible, key=lambda item: item.through_sequence, default=None)

    async def add_if_absent(
        self,
        summary: ConversationSummary,
    ) -> ConversationSummary:
        self.add_calls += 1
        authority = (
            summary.conversation_id,
            summary.agent_version_id,
            summary.from_sequence,
            summary.through_sequence,
            summary.summarizer_version,
            summary.token_counter_version,
        )
        async with self._lock:
            for existing in self._summaries:
                existing_authority = (
                    existing.conversation_id,
                    existing.agent_version_id,
                    existing.from_sequence,
                    existing.through_sequence,
                    existing.summarizer_version,
                    existing.token_counter_version,
                )
                if existing_authority == authority:
                    return existing
            self._summaries.append(summary)
            return summary


class FakeUnitOfWork:
    def __init__(self, factory: "FakeUnitOfWorkFactory") -> None:
        self._factory = factory
        self.agents = factory.agents
        self.conversations = factory.conversations
        self.turns = factory.turns
        self.summaries = factory.summaries

    async def __aenter__(self) -> Self:
        self._factory.active_transactions += 1
        self._factory.opened_transactions += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._factory.active_transactions -= 1

    async def commit(self) -> None:
        self._factory.commits += 1

    async def rollback(self) -> None:
        pass


class FakeUnitOfWorkFactory:
    def __init__(
        self,
        *,
        agents: FakeAgentRepository,
        conversations: FakeConversationRepository,
        turns: FakeTurnRepository,
        summaries: FakeSummaryRepository,
    ) -> None:
        self.agents = agents
        self.conversations = conversations
        self.turns = turns
        self.summaries = summaries
        self.active_transactions = 0
        self.opened_transactions = 0
        self.commits = 0

    def __call__(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self)


class RecordingSummarizer:
    version = "prefix-v1"

    def __init__(
        self,
        factory: FakeUnitOfWorkFactory,
        *,
        content: str = "ssss",
        failure: BaseException | None = None,
    ) -> None:
        self._factory = factory
        self._content = content
        self._failure = failure
        self.calls: list[tuple[tuple[Message, ...], int]] = []

    async def summarize(
        self,
        *,
        messages: tuple[Message, ...],
        max_tokens: int,
    ) -> str:
        assert self._factory.active_transactions == 0
        self.calls.append((messages, max_tokens))
        if self._failure is not None:
            raise self._failure
        return self._content


class BarrierSummarizer(RecordingSummarizer):
    def __init__(self, factory: FakeUnitOfWorkFactory) -> None:
        super().__init__(factory)
        self._both_entered = asyncio.Event()

    async def summarize(
        self,
        *,
        messages: tuple[Message, ...],
        max_tokens: int,
    ) -> str:
        assert self._factory.active_transactions == 0
        self.calls.append((messages, max_tokens))
        if len(self.calls) == 2:
            self._both_entered.set()
        await self._both_entered.wait()
        return self._content


@dataclass(frozen=True, slots=True)
class Scenario:
    now: datetime
    definition: AgentDefinition
    version: AgentVersion
    conversation: Conversation
    messages: tuple[Message, ...]
    turn: Turn
    factory: FakeUnitOfWorkFactory


def make_scenario(token_counts: list[int]) -> Scenario:
    now = datetime(2026, 7, 13, 22, 0, tzinfo=UTC)
    team_id = TeamId(uuid4())
    definition = AgentDefinition(
        id=AgentDefinitionId(uuid4()),
        team_id=team_id,
        name="Project Assistant",
        created_at=now,
    )
    version = AgentVersion(
        id=AgentVersionId(uuid4()),
        agent_definition_id=definition.id,
        version_number=1,
        context_policy=ContextPolicy(20, 4, 3, 4, 2),
        created_at=now,
    )
    conversation = Conversation(
        id=ConversationId(uuid4()),
        team_id=team_id,
        default_agent_version_id=version.id,
        status=ConversationStatus.ACTIVE,
        next_message_sequence=len(token_counts) + 1,
        created_at=now,
        updated_at=now,
    )
    messages = tuple(
        Message(
            id=MessageId(uuid4()),
            conversation_id=conversation.id,
            sequence=index,
            role=MessageRole.USER if index % 2 else MessageRole.ASSISTANT,
            content="x" * token_count,
            created_at=now,
        )
        for index, token_count in enumerate(token_counts, start=1)
    )
    turn = Turn(
        id=TurnId(uuid4()),
        conversation_id=conversation.id,
        input_message_id=messages[-1].id,
        agent_version_id=version.id,
        status=TurnStatus.RECEIVED,
        created_at=now,
    )
    factory = FakeUnitOfWorkFactory(
        agents=FakeAgentRepository(definition, version),
        conversations=FakeConversationRepository(conversation, messages),
        turns=FakeTurnRepository(turn),
        summaries=FakeSummaryRepository(),
    )
    return Scenario(now, definition, version, conversation, messages, turn, factory)


def make_use_case(
    scenario: Scenario,
    summarizer: RecordingSummarizer,
) -> BuildTurnContext:
    return BuildTurnContext(
        unit_of_work_factory=scenario.factory,
        token_counter=CharacterTokenCounter(),
        summarizer=summarizer,
        clock=FixedClock(scenario.now),
        summary_ids=SequenceIdGenerator(),
    )


def make_persisted_summary(scenario: Scenario) -> ConversationSummary:
    return ConversationSummary(
        id=ConversationSummaryId(uuid4()),
        conversation_id=scenario.conversation.id,
        agent_version_id=scenario.version.id,
        from_sequence=1,
        through_sequence=3,
        content="ssss",
        estimated_token_count=4,
        summarizer_version="prefix-v1",
        token_counter_version="character-v1",
        created_at=scenario.now,
    )


async def test_short_context_neither_reads_nor_creates_summary() -> None:
    scenario = make_scenario([2, 3, 3])
    summarizer = RecordingSummarizer(scenario.factory)

    context = await make_use_case(scenario, summarizer).execute(
        BuildTurnContextCommand(scenario.turn.id)
    )

    assert summarizer.calls == []
    assert scenario.factory.summaries.add_calls == 0
    assert scenario.factory.opened_transactions == 1
    assert scenario.factory.conversations.cutoffs == [3]
    assert context.used_input_tokens == 8


async def test_long_context_summarizes_without_open_transaction_and_persists() -> None:
    scenario = make_scenario([3, 3, 3, 4, 4])
    summarizer = RecordingSummarizer(scenario.factory)

    context = await make_use_case(scenario, summarizer).execute(
        BuildTurnContextCommand(scenario.turn.id)
    )

    assert len(summarizer.calls) == 1
    assert [message.sequence for message in summarizer.calls[0][0]] == [1, 2, 3]
    assert summarizer.calls[0][1] == 4
    assert scenario.factory.active_transactions == 0
    assert scenario.factory.opened_transactions == 3
    assert scenario.factory.commits == 1
    assert len(scenario.factory.summaries.summaries) == 1
    assert isinstance(context.items[0].source, SummaryContextSource)
    assert context.used_input_tokens == 12


async def test_compatible_summary_is_reused_without_summarization() -> None:
    scenario = make_scenario([3, 3, 3, 4, 4])
    persisted = make_persisted_summary(scenario)
    scenario.factory.summaries._summaries.append(persisted)
    summarizer = RecordingSummarizer(scenario.factory)

    context = await make_use_case(scenario, summarizer).execute(
        BuildTurnContextCommand(scenario.turn.id)
    )

    assert summarizer.calls == []
    assert scenario.factory.summaries.add_calls == 0
    assert scenario.factory.opened_transactions == 2
    assert context.items[0].source == persisted.source


async def test_messages_after_turn_input_are_excluded() -> None:
    scenario = make_scenario([2, 3, 3])
    later = Message(
        id=MessageId(uuid4()),
        conversation_id=scenario.conversation.id,
        sequence=4,
        role=MessageRole.ASSISTANT,
        content="later",
        created_at=scenario.now,
    )
    scenario.factory.conversations.messages += (later,)
    summarizer = RecordingSummarizer(scenario.factory)

    context = await make_use_case(scenario, summarizer).execute(
        BuildTurnContextCommand(scenario.turn.id)
    )

    sequences = [
        item.source.sequence
        for item in context.items
        if isinstance(item.source, MessageContextSource)
    ]
    assert sequences == [1, 2, 3]
    assert scenario.factory.conversations.cutoffs == [3]


async def test_incompatible_agent_version_summary_is_not_reused() -> None:
    scenario = make_scenario([3, 3, 3, 4, 4])
    incompatible = make_persisted_summary(scenario)
    incompatible = ConversationSummary(
        id=incompatible.id,
        conversation_id=incompatible.conversation_id,
        agent_version_id=AgentVersionId(uuid4()),
        from_sequence=incompatible.from_sequence,
        through_sequence=incompatible.through_sequence,
        content=incompatible.content,
        estimated_token_count=incompatible.estimated_token_count,
        summarizer_version=incompatible.summarizer_version,
        token_counter_version=incompatible.token_counter_version,
        created_at=incompatible.created_at,
    )
    scenario.factory.summaries._summaries.append(incompatible)
    summarizer = RecordingSummarizer(scenario.factory)

    await make_use_case(scenario, summarizer).execute(BuildTurnContextCommand(scenario.turn.id))

    assert len(summarizer.calls) == 1
    assert len(scenario.factory.summaries.summaries) == 2


async def test_oversized_summary_is_validated_before_persistence() -> None:
    scenario = make_scenario([3, 3, 3, 4, 4])
    summarizer = RecordingSummarizer(scenario.factory, content="too-large")

    with pytest.raises(ContextBudgetExceededError):
        await make_use_case(scenario, summarizer).execute(BuildTurnContextCommand(scenario.turn.id))

    assert scenario.factory.summaries.add_calls == 0
    assert scenario.factory.commits == 0


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("summarizer failed"), asyncio.CancelledError()],
)
async def test_summarizer_failure_or_cancellation_persists_nothing(
    failure: BaseException,
) -> None:
    scenario = make_scenario([3, 3, 3, 4, 4])
    summarizer = RecordingSummarizer(scenario.factory, failure=failure)

    with pytest.raises(type(failure)):
        await make_use_case(scenario, summarizer).execute(BuildTurnContextCommand(scenario.turn.id))

    assert scenario.factory.active_transactions == 0
    assert scenario.factory.summaries.add_calls == 0
    assert scenario.factory.summaries.summaries == ()
    assert scenario.factory.commits == 0


async def test_concurrent_builders_converge_on_one_authoritative_summary() -> None:
    scenario = make_scenario([3, 3, 3, 4, 4])
    summarizer = BarrierSummarizer(scenario.factory)
    left = make_use_case(scenario, summarizer)
    right = make_use_case(scenario, summarizer)

    contexts = await asyncio.gather(
        left.execute(BuildTurnContextCommand(scenario.turn.id)),
        right.execute(BuildTurnContextCommand(scenario.turn.id)),
    )

    assert len(summarizer.calls) == 2
    assert scenario.factory.summaries.add_calls == 2
    assert len(scenario.factory.summaries.summaries) == 1
    assert contexts[0].items[0].source == contexts[1].items[0].source


@pytest.mark.parametrize(
    ("missing", "error_type"),
    [
        ("turn", TurnNotFoundError),
        ("conversation", ConversationNotFoundError),
        ("agent_version", AgentVersionNotFoundError),
        ("agent_definition", AgentDefinitionNotFoundError),
    ],
)
async def test_missing_snapshot_dependencies_fail_explicitly(
    missing: str,
    error_type: type[Exception],
) -> None:
    scenario = make_scenario([2, 3, 3])
    if missing == "turn":
        scenario.factory.turns.turn = None
    elif missing == "conversation":
        scenario.factory.conversations.conversation = None
    elif missing == "agent_version":
        scenario.factory.agents.version = None
    else:
        scenario.factory.agents.definition = None
    summarizer = RecordingSummarizer(scenario.factory)

    with pytest.raises(error_type):
        await make_use_case(scenario, summarizer).execute(BuildTurnContextCommand(scenario.turn.id))


async def test_cross_team_agent_version_is_rejected_before_summary_access() -> None:
    scenario = make_scenario([3, 3, 3, 4, 4])
    definition = scenario.factory.agents.definition
    assert definition is not None
    scenario.factory.agents.definition = AgentDefinition(
        id=definition.id,
        team_id=TeamId(uuid4()),
        name=definition.name,
        created_at=definition.created_at,
    )
    summarizer = RecordingSummarizer(scenario.factory)

    with pytest.raises(AgentTeamMismatchError):
        await make_use_case(scenario, summarizer).execute(BuildTurnContextCommand(scenario.turn.id))

    assert summarizer.calls == []
    assert scenario.factory.summaries.add_calls == 0
