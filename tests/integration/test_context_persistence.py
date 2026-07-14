"""PostgreSQL proofs for context policy and summary persistence."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.domain.agents import AgentVersion
from switchboard.domain.context import ContextPolicy, ConversationSummary
from switchboard.domain.conversations import MessageRole
from switchboard.domain.identifiers import (
    AgentVersionId,
    ConversationId,
    ConversationSummaryId,
    MessageId,
)
from tests.integration.support import seed_turn


def make_summary(
    *,
    turn_agent_version_id: AgentVersionId,
    conversation_id: ConversationId,
    through_sequence: int,
    now: datetime,
    token_counter_version: str = "word-count-v1",
    content: str = "Earlier requirements and decisions.",
) -> ConversationSummary:
    return ConversationSummary(
        id=ConversationSummaryId(uuid4()),
        conversation_id=conversation_id,
        agent_version_id=turn_agent_version_id,
        from_sequence=1,
        through_sequence=through_sequence,
        content=content,
        estimated_token_count=5,
        summarizer_version="prefix-v1",
        token_counter_version=token_counter_version,
        created_at=now,
    )


async def append_message(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
    *,
    conversation_id: ConversationId,
    content: str,
    now: datetime,
) -> None:
    async with unit_of_work_factory() as unit_of_work:
        await unit_of_work.conversations.append_message(
            conversation_id=conversation_id,
            message_id=MessageId(uuid4()),
            role=MessageRole.ASSISTANT,
            content=content,
            created_at=now,
        )
        await unit_of_work.commit()


async def test_policy_round_trip_and_inclusive_message_cutoff(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 20, 0, tzinfo=UTC)
    turn, _ = await seed_turn(unit_of_work_factory, now=now)
    await append_message(
        unit_of_work_factory,
        conversation_id=turn.conversation_id,
        content="Second message.",
        now=now + timedelta(seconds=1),
    )
    await append_message(
        unit_of_work_factory,
        conversation_id=turn.conversation_id,
        content="Later concurrent message.",
        now=now + timedelta(seconds=2),
    )

    async with unit_of_work_factory() as unit_of_work:
        version = await unit_of_work.agents.get_version(turn.agent_version_id)
        messages = await unit_of_work.conversations.list_messages_through(
            conversation_id=turn.conversation_id,
            through_sequence=2,
        )

    assert version is not None
    assert version.context_policy == ContextPolicy(4096, 512, 256, 256, 1)
    assert [message.sequence for message in messages] == [1, 2]
    assert [message.content for message in messages] == [
        "Show overdue work.",
        "Second message.",
    ]


async def test_summary_reuse_requires_compatible_provenance(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 20, 10, tzinfo=UTC)
    turn, _ = await seed_turn(unit_of_work_factory, now=now)
    await append_message(
        unit_of_work_factory,
        conversation_id=turn.conversation_id,
        content="Second message.",
        now=now + timedelta(seconds=1),
    )
    summary = make_summary(
        turn_agent_version_id=turn.agent_version_id,
        conversation_id=turn.conversation_id,
        through_sequence=2,
        now=now,
    )

    async with unit_of_work_factory() as unit_of_work:
        persisted = await unit_of_work.summaries.add_if_absent(summary)
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        compatible = await unit_of_work.summaries.get_latest_compatible(
            conversation_id=turn.conversation_id,
            agent_version_id=turn.agent_version_id,
            through_sequence=3,
            summarizer_version="prefix-v1",
            token_counter_version="word-count-v1",
        )
        changed_counter = await unit_of_work.summaries.get_latest_compatible(
            conversation_id=turn.conversation_id,
            agent_version_id=turn.agent_version_id,
            through_sequence=3,
            summarizer_version="prefix-v1",
            token_counter_version="provider-counter-v2",
        )

    assert persisted == summary
    assert compatible == summary
    assert changed_counter is None


async def test_concurrent_summary_creators_converge_on_one_authority(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 20, 20, tzinfo=UTC)
    turn, _ = await seed_turn(unit_of_work_factory, now=now)
    left = make_summary(
        turn_agent_version_id=turn.agent_version_id,
        conversation_id=turn.conversation_id,
        through_sequence=1,
        now=now,
        content="Left candidate.",
    )
    right = make_summary(
        turn_agent_version_id=turn.agent_version_id,
        conversation_id=turn.conversation_id,
        through_sequence=1,
        now=now,
        content="Right candidate.",
    )

    async def persist(candidate: ConversationSummary) -> ConversationSummary:
        async with unit_of_work_factory() as unit_of_work:
            winner = await unit_of_work.summaries.add_if_absent(candidate)
            await unit_of_work.commit()
            return winner

    first, second = await asyncio.gather(persist(left), persist(right))

    assert first == second
    assert first in {left, right}


async def test_database_rejects_summary_coverage_from_another_conversation(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 20, 30, tzinfo=UTC)
    first_turn, _ = await seed_turn(unit_of_work_factory, now=now)
    second_turn, _ = await seed_turn(unit_of_work_factory, now=now)
    await append_message(
        unit_of_work_factory,
        conversation_id=second_turn.conversation_id,
        content="Only the second conversation has sequence two.",
        now=now + timedelta(seconds=1),
    )
    invalid = make_summary(
        turn_agent_version_id=first_turn.agent_version_id,
        conversation_id=first_turn.conversation_id,
        through_sequence=2,
        now=now,
    )

    async with unit_of_work_factory() as unit_of_work:
        with pytest.raises(IntegrityError):
            await unit_of_work.summaries.add_if_absent(invalid)
        await unit_of_work.rollback()


async def test_summary_is_not_reused_by_another_agent_version(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    now = datetime(2026, 7, 13, 20, 40, tzinfo=UTC)
    turn, _ = await seed_turn(unit_of_work_factory, now=now)
    summary = make_summary(
        turn_agent_version_id=turn.agent_version_id,
        conversation_id=turn.conversation_id,
        through_sequence=1,
        now=now,
    )

    async with unit_of_work_factory() as unit_of_work:
        original = await unit_of_work.agents.get_version(turn.agent_version_id)
        assert original is not None
        replacement = AgentVersion(
            id=AgentVersionId(uuid4()),
            agent_definition_id=original.agent_definition_id,
            version_number=2,
            context_policy=ContextPolicy(8192, 1024, 256, 512, 2),
            created_at=now,
        )
        await unit_of_work.agents.add_version(replacement)
        await unit_of_work.summaries.add_if_absent(summary)
        await unit_of_work.commit()

    async with unit_of_work_factory() as unit_of_work:
        incompatible = await unit_of_work.summaries.get_latest_compatible(
            conversation_id=turn.conversation_id,
            agent_version_id=replacement.id,
            through_sequence=1,
            summarizer_version="prefix-v1",
            token_counter_version="word-count-v1",
        )

    assert incompatible is None
