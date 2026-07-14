"""PostgreSQL proof for durable command-receipt authority."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from switchboard.adapters.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory
from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.command_receipts import (
    CREATE_CONVERSATION_SCOPE,
    CommandOperation,
    CommandReceipt,
)
from switchboard.domain.context import ContextPolicy
from switchboard.domain.conversations import Conversation, ConversationStatus, MessageRole
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
from switchboard.domain.turns import Turn, TurnAttempt, TurnAttemptStatus, TurnStatus
from tests.integration.support import seed_turn

NOW = datetime(2026, 7, 14, 18, 0, tzinfo=UTC)


def make_receipt(
    *,
    team_id: TeamId,
    conversation_id: ConversationId,
    message_id: MessageId,
    turn_id: TurnId,
    attempt_id: TurnAttemptId,
    receipt_id: CommandReceiptId | None = None,
    request_fingerprint: str = "b" * 64,
) -> CommandReceipt:
    return CommandReceipt(
        id=receipt_id or CommandReceiptId(uuid4()),
        team_id=team_id,
        operation=CommandOperation.CREATE_CONVERSATION,
        command_scope=CREATE_CONVERSATION_SCOPE,
        idempotency_key_hash="a" * 64,
        request_fingerprint=request_fingerprint,
        conversation_id=conversation_id,
        message_id=message_id,
        turn_id=turn_id,
        attempt_id=attempt_id,
        created_at=NOW,
    )


async def receipt_for_seeded_turn(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> CommandReceipt:
    turn, attempt = await seed_turn(unit_of_work_factory, now=NOW)
    async with unit_of_work_factory() as unit_of_work:
        conversation = await unit_of_work.conversations.get(turn.conversation_id)
    assert conversation is not None
    return make_receipt(
        team_id=conversation.team_id,
        conversation_id=conversation.id,
        message_id=turn.input_message_id,
        turn_id=turn.id,
        attempt_id=attempt.id,
    )


async def test_receipt_can_be_claimed_before_atomic_result_graph(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    team_id = TeamId(uuid4())
    definition = AgentDefinition(
        id=AgentDefinitionId(uuid4()),
        team_id=team_id,
        name="Receipt test agent",
        created_at=NOW,
    )
    version = AgentVersion(
        id=AgentVersionId(uuid4()),
        agent_definition_id=definition.id,
        version_number=1,
        context_policy=ContextPolicy(4096, 512, 256, 256, 1),
        created_at=NOW,
    )
    conversation = Conversation(
        id=ConversationId(uuid4()),
        team_id=team_id,
        default_agent_version_id=version.id,
        status=ConversationStatus.ACTIVE,
        next_message_sequence=1,
        created_at=NOW,
        updated_at=NOW,
    )
    message_id = MessageId(uuid4())
    turn_id = TurnId(uuid4())
    attempt_id = TurnAttemptId(uuid4())
    receipt = make_receipt(
        team_id=team_id,
        conversation_id=conversation.id,
        message_id=message_id,
        turn_id=turn_id,
        attempt_id=attempt_id,
    )

    async with unit_of_work_factory() as unit_of_work:
        claimed, created = await unit_of_work.command_receipts.add_or_get(receipt)
        await unit_of_work.agents.add_definition(definition)
        await unit_of_work.agents.add_version(version)
        await unit_of_work.conversations.add(conversation)
        message = await unit_of_work.conversations.append_message(
            conversation_id=conversation.id,
            message_id=message_id,
            role=MessageRole.USER,
            content="Start atomically.",
            created_at=NOW,
        )
        await unit_of_work.turns.add(
            Turn(
                id=turn_id,
                conversation_id=conversation.id,
                input_message_id=message.id,
                agent_version_id=version.id,
                status=TurnStatus.RECEIVED,
                created_at=NOW,
            )
        )
        await unit_of_work.turns.add_attempt(
            TurnAttempt(
                id=attempt_id,
                turn_id=turn_id,
                attempt_number=1,
                status=TurnAttemptStatus.PENDING,
                created_at=NOW,
            )
        )
        await unit_of_work.commit()

    assert created
    assert claimed == receipt
    async with unit_of_work_factory() as unit_of_work:
        persisted = await unit_of_work.command_receipts.get_by_authority(
            team_id=team_id,
            operation=CommandOperation.CREATE_CONVERSATION,
            command_scope=CREATE_CONVERSATION_SCOPE,
            idempotency_key_hash=receipt.idempotency_key_hash,
        )
    assert persisted == receipt


async def test_duplicate_authority_returns_original_receipt(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    receipt = await receipt_for_seeded_turn(unit_of_work_factory)
    conflicting_candidate = make_receipt(
        team_id=receipt.team_id,
        conversation_id=receipt.conversation_id,
        message_id=receipt.message_id,
        turn_id=receipt.turn_id,
        attempt_id=receipt.attempt_id,
        request_fingerprint="c" * 64,
    )

    async with unit_of_work_factory() as unit_of_work:
        first, first_created = await unit_of_work.command_receipts.add_or_get(receipt)
        await unit_of_work.commit()
    async with unit_of_work_factory() as unit_of_work:
        replay, replay_created = await unit_of_work.command_receipts.add_or_get(
            conflicting_candidate
        )
        await unit_of_work.commit()

    assert first_created
    assert not replay_created
    assert first == receipt
    assert replay == receipt


async def test_concurrent_duplicate_claims_converge_on_one_authority(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    receipt = await receipt_for_seeded_turn(unit_of_work_factory)
    candidates = (
        receipt,
        make_receipt(
            team_id=receipt.team_id,
            conversation_id=receipt.conversation_id,
            message_id=receipt.message_id,
            turn_id=receipt.turn_id,
            attempt_id=receipt.attempt_id,
            request_fingerprint="c" * 64,
        ),
    )

    async def claim(candidate: CommandReceipt) -> tuple[CommandReceipt, bool]:
        async with unit_of_work_factory() as unit_of_work:
            result = await unit_of_work.command_receipts.add_or_get(candidate)
            await unit_of_work.commit()
            return result

    results = await asyncio.gather(*(claim(candidate) for candidate in candidates))

    assert sum(created for _, created in results) == 1
    assert len({claimed.id for claimed, _ in results}) == 1
    assert len({claimed.request_fingerprint for claimed, _ in results}) == 1


async def test_uncommitted_receipt_rolls_back(
    unit_of_work_factory: SqlAlchemyUnitOfWorkFactory,
) -> None:
    receipt = await receipt_for_seeded_turn(unit_of_work_factory)

    async with unit_of_work_factory() as unit_of_work:
        _, created = await unit_of_work.command_receipts.add_or_get(receipt)
        assert created

    async with unit_of_work_factory() as unit_of_work:
        persisted = await unit_of_work.command_receipts.get_by_authority(
            team_id=receipt.team_id,
            operation=receipt.operation,
            command_scope=receipt.command_scope,
            idempotency_key_hash=receipt.idempotency_key_hash,
        )

    assert persisted is None
