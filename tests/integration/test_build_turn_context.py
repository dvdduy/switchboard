"""PostgreSQL end-to-end proofs for reproducible bounded context."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from switchboard.adapters.context.deterministic_summarizer import (
    DeterministicPrefixSummarizer,
)
from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
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


class SummaryIdGenerator:
    def new(self) -> ConversationSummaryId:
        return ConversationSummaryId(uuid4())


class CountingSummarizer:
    def __init__(self, counter: CharacterTokenCounter) -> None:
        self._delegate = DeterministicPrefixSummarizer(counter)
        self.calls = 0

    @property
    def version(self) -> str:
        return self._delegate.version

    async def summarize(
        self,
        *,
        messages: tuple[Message, ...],
        max_tokens: int,
    ) -> str:
        self.calls += 1
        return await self._delegate.summarize(
            messages=messages,
            max_tokens=max_tokens,
        )


async def seed_long_context_turn(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    now: datetime,
) -> tuple[Turn, AgentVersion]:
    team_id = TeamId(uuid4())
    definition = AgentDefinition(
        id=AgentDefinitionId(uuid4()),
        team_id=team_id,
        name="Context Assistant",
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
        next_message_sequence=1,
        created_at=now,
        updated_at=now,
    )

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.agents.add_definition(definition)
        await unit_of_work.agents.add_version(version)
        await unit_of_work.conversations.add(conversation)

        messages: list[Message] = []
        for index, count in enumerate([3, 3, 3, 4, 4], start=1):
            message = await unit_of_work.conversations.append_message(
                conversation_id=conversation.id,
                message_id=MessageId(uuid4()),
                role=MessageRole.USER if index % 2 else MessageRole.ASSISTANT,
                content="x" * count,
                created_at=now,
            )
            messages.append(message)

        turn = Turn(
            id=TurnId(uuid4()),
            conversation_id=conversation.id,
            input_message_id=messages[-1].id,
            agent_version_id=version.id,
            status=TurnStatus.RECEIVED,
            created_at=now,
        )
        await unit_of_work.turns.add(turn)
        await unit_of_work.commit()

    return turn, version


async def test_context_reconstructs_under_budget_and_reuses_persisted_summary(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 23, 0, tzinfo=UTC)
    turn, version = await seed_long_context_turn(unit_of_work_factory, now=now)
    counter = CharacterTokenCounter()
    summarizer = CountingSummarizer(counter)
    use_case = BuildTurnContext(
        unit_of_work_factory=unit_of_work_factory,
        token_counter=counter,
        summarizer=summarizer,
        clock=FixedClock(now),
        summary_ids=SummaryIdGenerator(),
    )

    # Commit history after the turn input before context is first reconstructed.
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.conversations.append_message(
            conversation_id=turn.conversation_id,
            message_id=MessageId(uuid4()),
            role=MessageRole.ASSISTANT,
            content="later",
            created_at=now + timedelta(seconds=1),
        )
        await unit_of_work.commit()

    first = await use_case.execute(BuildTurnContextCommand(turn.id))

    assert summarizer.calls == 1
    assert first.used_input_tokens <= version.context_policy.available_input_tokens
    assert isinstance(first.items[0].source, SummaryContextSource)
    assert first.items[0].source.through_sequence == 3
    message_sequences = [
        item.source.sequence
        for item in first.items
        if isinstance(item.source, MessageContextSource)
    ]
    assert message_sequences == [4, 5]
    assert all(item.mandatory for item in first.items[1:])

    # Reopen persistence and prove both the durable artifact and deterministic reuse.
    async with unit_of_work_factory() as unit_of_work:
        persisted = await unit_of_work.summaries.get_latest_compatible(
            conversation_id=turn.conversation_id,
            agent_version_id=version.id,
            through_sequence=3,
            summarizer_version=summarizer.version,
            token_counter_version=counter.version,
        )
    assert persisted is not None
    assert persisted.through_sequence == 3

    second = await use_case.execute(BuildTurnContextCommand(turn.id))

    assert summarizer.calls == 1
    assert second == first


async def test_rolled_back_summary_is_not_durable(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 23, 10, tzinfo=UTC)
    turn, version = await seed_long_context_turn(unit_of_work_factory, now=now)
    summary = ConversationSummary(
        id=ConversationSummaryId(uuid4()),
        conversation_id=turn.conversation_id,
        agent_version_id=version.id,
        from_sequence=1,
        through_sequence=1,
        content="x",
        estimated_token_count=1,
        summarizer_version="rollback-v1",
        token_counter_version="character-v1",
        created_at=now,
    )

    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.summaries.add_if_absent(summary)
        await unit_of_work.rollback()

    async with unit_of_work_factory() as unit_of_work:
        persisted = await unit_of_work.summaries.get_latest_compatible(
            conversation_id=turn.conversation_id,
            agent_version_id=version.id,
            through_sequence=1,
            summarizer_version="rollback-v1",
            token_counter_version="character-v1",
        )

    assert persisted is None
