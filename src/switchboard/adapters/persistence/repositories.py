"""SQLAlchemy implementations of application repository ports."""

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from switchboard.adapters.persistence.schema import (
    agent_definitions,
    agent_tool_bindings,
    agent_versions,
    command_receipts,
    conversation_summaries,
    conversations,
    execution_events,
    messages,
    tool_conformance_case_results,
    tool_conformance_runs,
    tool_definitions,
    tool_invocations,
    tool_version_states,
    tool_versions,
    turn_attempts,
    turns,
)
from switchboard.adapters.persistence.translators import (
    agent_definition_from_record,
    agent_definition_to_record,
    agent_tool_binding_from_record,
    agent_tool_binding_to_record,
    agent_version_from_record,
    agent_version_to_record,
    command_receipt_from_record,
    command_receipt_to_record,
    conversation_from_record,
    conversation_summary_from_record,
    conversation_summary_to_record,
    conversation_to_record,
    execution_event_from_record,
    execution_event_to_record,
    message_from_record,
    message_to_record,
    tool_conformance_case_result_from_record,
    tool_conformance_case_result_to_record,
    tool_conformance_run_from_record,
    tool_conformance_run_to_record,
    tool_definition_from_record,
    tool_definition_to_record,
    tool_invocation_from_record,
    tool_invocation_to_record,
    tool_version_from_record,
    tool_version_state_from_record,
    tool_version_state_to_record,
    tool_version_to_record,
    turn_attempt_from_record,
    turn_attempt_to_record,
    turn_from_record,
    turn_to_record,
)
from switchboard.application.errors import (
    ConversationNotFoundError,
    ToolDefinitionNotFoundError,
    ToolInvocationLifecycleConflictError,
    ToolVersionLifecycleConflictError,
    TurnAttemptLifecycleConflictError,
    TurnEventStateError,
    TurnLifecycleConflictError,
    TurnNotFoundError,
)
from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.command_receipts import CommandOperation, CommandReceipt
from switchboard.domain.context import ConversationSummary
from switchboard.domain.conversations import Conversation, Message, MessageRole
from switchboard.domain.execution_events import (
    ExecutionEvent,
    ExecutionEventKind,
)
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentToolBindingId,
    AgentVersionId,
    ConversationId,
    ExecutionEventId,
    MessageId,
    TeamId,
    ToolConformanceRunId,
    ToolDefinitionId,
    ToolInvocationId,
    ToolVersionId,
    TurnAttemptId,
    TurnId,
)
from switchboard.domain.json_values import mutable_json_value
from switchboard.domain.tool_invocations import ToolInvocation
from switchboard.domain.tools import (
    AgentToolBinding,
    EligibleTool,
    ToolConformanceCaseResult,
    ToolConformanceRun,
    ToolConformanceStatus,
    ToolDefinition,
    ToolLifecycleStatus,
    ToolManifest,
    ToolVersion,
    ToolVersionState,
)
from switchboard.domain.turns import Turn, TurnAttempt, TurnStatus


def _matches_nullable(
    column: ColumnElement[object],
    value: object,
) -> ColumnElement[bool]:
    if value is None:
        return column.is_(None)

    return column == value


class SqlAlchemyAgentRepository:
    """Persists agent definitions and immutable versions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_definition(
        self,
        definition: AgentDefinition,
    ) -> None:
        await self._session.execute(
            insert(agent_definitions).values(agent_definition_to_record(definition))
        )

    async def add_version(
        self,
        version: AgentVersion,
    ) -> None:
        await self._session.execute(insert(agent_versions).values(agent_version_to_record(version)))

    async def add_next_version_from(
        self,
        *,
        agent_version_id: AgentVersionId,
        base_version: AgentVersion,
        created_at: datetime,
    ) -> AgentVersion:
        definition_result = await self._session.execute(
            select(agent_definitions.c.id)
            .where(agent_definitions.c.id == base_version.agent_definition_id)
            .with_for_update()
        )
        if definition_result.scalar_one_or_none() is None:
            raise ValueError("base agent definition was not found")
        version_result = await self._session.execute(
            select(func.coalesce(func.max(agent_versions.c.version_number), 0)).where(
                agent_versions.c.agent_definition_id == base_version.agent_definition_id
            )
        )
        version = AgentVersion(
            id=agent_version_id,
            agent_definition_id=base_version.agent_definition_id,
            version_number=version_result.scalar_one() + 1,
            context_policy=base_version.context_policy,
            created_at=created_at,
        )
        await self._session.execute(insert(agent_versions).values(agent_version_to_record(version)))
        return version

    async def get_version(
        self,
        agent_version_id: AgentVersionId,
    ) -> AgentVersion | None:
        statement = select(agent_versions).where(agent_versions.c.id == agent_version_id)

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            return None

        return agent_version_from_record(record)

    async def get_definition(
        self,
        agent_definition_id: AgentDefinitionId,
    ) -> AgentDefinition | None:
        statement = select(agent_definitions).where(agent_definitions.c.id == agent_definition_id)

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            return None

        return agent_definition_from_record(record)


class SqlAlchemyConversationRepository:
    """Persists conversations and allocates ordered messages."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        conversation: Conversation,
    ) -> None:
        await self._session.execute(
            insert(conversations).values(conversation_to_record(conversation))
        )

    async def get(
        self,
        conversation_id: ConversationId,
    ) -> Conversation | None:
        statement = select(conversations).where(conversations.c.id == conversation_id)

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            return None

        return conversation_from_record(record)

    async def append_message(
        self,
        *,
        conversation_id: ConversationId,
        message_id: MessageId,
        role: MessageRole,
        content: str,
        created_at: datetime,
    ) -> Message:
        statement = (
            select(conversations).where(conversations.c.id == conversation_id).with_for_update()
        )

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            raise ConversationNotFoundError(f"conversation {conversation_id} was not found")

        conversation = conversation_from_record(record)

        updated_conversation, allocated_sequence = conversation.allocate_message_sequence(
            at=created_at
        )

        message = Message(
            id=message_id,
            conversation_id=conversation_id,
            sequence=allocated_sequence,
            role=role,
            content=content,
            created_at=created_at,
        )

        await self._session.execute(
            update(conversations)
            .where(conversations.c.id == conversation_id)
            .values(
                next_message_sequence=(updated_conversation.next_message_sequence),
                updated_at=updated_conversation.updated_at,
            )
        )

        await self._session.execute(insert(messages).values(message_to_record(message)))

        return message

    async def list_messages(
        self,
        conversation_id: ConversationId,
    ) -> tuple[Message, ...]:
        statement = (
            select(messages)
            .where(messages.c.conversation_id == conversation_id)
            .order_by(messages.c.sequence)
        )

        result = await self._session.execute(statement)

        return tuple(message_from_record(record) for record in result.mappings())

    async def get_message(
        self,
        *,
        conversation_id: ConversationId,
        message_id: MessageId,
    ) -> Message | None:
        statement = select(messages).where(
            messages.c.conversation_id == conversation_id,
            messages.c.id == message_id,
        )
        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()
        return None if record is None else message_from_record(record)

    async def list_messages_through(
        self,
        *,
        conversation_id: ConversationId,
        through_sequence: int,
    ) -> tuple[Message, ...]:
        if through_sequence <= 0:
            raise ValueError("through_sequence must be greater than zero")

        statement = (
            select(messages)
            .where(
                messages.c.conversation_id == conversation_id,
                messages.c.sequence <= through_sequence,
            )
            .order_by(messages.c.sequence)
        )
        result = await self._session.execute(statement)
        return tuple(message_from_record(record) for record in result.mappings())

    async def list_messages_after(
        self,
        *,
        conversation_id: ConversationId,
        after_sequence: int,
        limit: int,
    ) -> tuple[Message, ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")
        if limit <= 0:
            raise ValueError("limit must be greater than zero")

        statement = (
            select(messages)
            .where(
                messages.c.conversation_id == conversation_id,
                messages.c.sequence > after_sequence,
            )
            .order_by(messages.c.sequence)
            .limit(limit)
        )
        result = await self._session.execute(statement)
        return tuple(message_from_record(record) for record in result.mappings())


class SqlAlchemyCommandReceiptRepository:
    """Persists one immutable authority for each idempotent command scope."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_or_get(
        self,
        receipt: CommandReceipt,
    ) -> tuple[CommandReceipt, bool]:
        statement = (
            postgresql_insert(command_receipts)
            .values(command_receipt_to_record(receipt))
            .on_conflict_do_nothing(constraint="command_receipt_authority")
            .returning(*command_receipts.c)
        )
        result = await self._session.execute(statement)
        inserted_record = result.mappings().one_or_none()
        if inserted_record is not None:
            return command_receipt_from_record(inserted_record), True

        authority = await self.get_by_authority(
            team_id=receipt.team_id,
            operation=receipt.operation,
            command_scope=receipt.command_scope,
            idempotency_key_hash=receipt.idempotency_key_hash,
        )
        if authority is None:
            raise RuntimeError("command receipt authority disappeared after conflict")
        return authority, False

    async def get_by_authority(
        self,
        *,
        team_id: TeamId,
        operation: CommandOperation,
        command_scope: str,
        idempotency_key_hash: str,
    ) -> CommandReceipt | None:
        statement = select(command_receipts).where(
            command_receipts.c.team_id == team_id,
            command_receipts.c.operation == operation.value,
            command_receipts.c.command_scope == command_scope,
            command_receipts.c.idempotency_key_hash == idempotency_key_hash,
        )
        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()
        return None if record is None else command_receipt_from_record(record)


class SqlAlchemyConversationSummaryRepository:
    """Persists immutable summaries with one winner per authority key."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_if_absent(
        self,
        summary: ConversationSummary,
    ) -> ConversationSummary:
        statement = (
            postgresql_insert(conversation_summaries)
            .values(conversation_summary_to_record(summary))
            .on_conflict_do_nothing(constraint="conversation_summary_authority")
            .returning(*conversation_summaries.c)
        )
        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is not None:
            return conversation_summary_from_record(record)

        winner_statement = select(conversation_summaries).where(
            conversation_summaries.c.conversation_id == summary.conversation_id,
            conversation_summaries.c.agent_version_id == summary.agent_version_id,
            conversation_summaries.c.from_sequence == summary.from_sequence,
            conversation_summaries.c.through_sequence == summary.through_sequence,
            conversation_summaries.c.summarizer_version == summary.summarizer_version,
            conversation_summaries.c.token_counter_version == summary.token_counter_version,
        )
        winner_result = await self._session.execute(winner_statement)
        return conversation_summary_from_record(winner_result.mappings().one())

    async def get_latest_compatible(
        self,
        *,
        conversation_id: ConversationId,
        agent_version_id: AgentVersionId,
        through_sequence: int,
        summarizer_version: str,
        token_counter_version: str,
    ) -> ConversationSummary | None:
        if through_sequence <= 0:
            raise ValueError("through_sequence must be greater than zero")

        statement = (
            select(conversation_summaries)
            .where(
                conversation_summaries.c.conversation_id == conversation_id,
                conversation_summaries.c.agent_version_id == agent_version_id,
                conversation_summaries.c.through_sequence <= through_sequence,
                conversation_summaries.c.summarizer_version == summarizer_version,
                conversation_summaries.c.token_counter_version == token_counter_version,
            )
            .order_by(conversation_summaries.c.through_sequence.desc())
            .limit(1)
        )
        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()
        return None if record is None else conversation_summary_from_record(record)


class SqlAlchemyTurnRepository:
    """Persists logical turns and physical attempts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        turn: Turn,
    ) -> None:
        await self._session.execute(insert(turns).values(turn_to_record(turn)))

    async def get(
        self,
        turn_id: TurnId,
    ) -> Turn | None:
        statement = select(turns).where(turns.c.id == turn_id)

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            return None

        return turn_from_record(record)

    async def add_attempt(
        self,
        attempt: TurnAttempt,
    ) -> None:
        await self._session.execute(insert(turn_attempts).values(turn_attempt_to_record(attempt)))

    async def list_attempts(
        self,
        turn_id: TurnId,
    ) -> tuple[TurnAttempt, ...]:
        statement = (
            select(turn_attempts)
            .where(turn_attempts.c.turn_id == turn_id)
            .order_by(turn_attempts.c.attempt_number)
        )

        result = await self._session.execute(statement)

        return tuple(turn_attempt_from_record(record) for record in result.mappings())

    async def update_turn_lifecycle(
        self,
        *,
        previous: Turn,
        updated: Turn,
    ) -> None:
        if previous.id != updated.id:
            raise ValueError("turn lifecycle transition must preserve identity")

        result = await self._session.execute(
            update(turns)
            .where(
                turns.c.id == previous.id,
                turns.c.status == previous.status.value,
                _matches_nullable(
                    turns.c.completed_at,
                    previous.completed_at,
                ),
            )
            .values(
                status=updated.status.value,
                completed_at=updated.completed_at,
            )
            .returning(turns.c.id)
        )

        updated_id = result.scalar_one_or_none()

        if updated_id is None:
            raise TurnLifecycleConflictError(
                f"turn {previous.id} lifecycle changed after it was read"
            )

    async def get_attempt(
        self,
        attempt_id: TurnAttemptId,
    ) -> TurnAttempt | None:
        statement = select(turn_attempts).where(turn_attempts.c.id == attempt_id)

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            return None

        return turn_attempt_from_record(record)

    async def update_attempt_lifecycle(
        self,
        *,
        previous: TurnAttempt,
        updated: TurnAttempt,
    ) -> None:
        if previous.id != updated.id:
            raise ValueError("attempt lifecycle transition must preserve identity")

        result = await self._session.execute(
            update(turn_attempts)
            .where(
                turn_attempts.c.id == previous.id,
                turn_attempts.c.status == previous.status.value,
                _matches_nullable(
                    turn_attempts.c.started_at,
                    previous.started_at,
                ),
                _matches_nullable(
                    turn_attempts.c.completed_at,
                    previous.completed_at,
                ),
                _matches_nullable(
                    turn_attempts.c.failure_code,
                    previous.failure_code,
                ),
            )
            .values(
                status=updated.status.value,
                started_at=updated.started_at,
                completed_at=updated.completed_at,
                failure_code=updated.failure_code,
            )
            .returning(turn_attempts.c.id)
        )

        updated_id = result.scalar_one_or_none()

        if updated_id is None:
            raise TurnAttemptLifecycleConflictError(
                f"turn attempt {previous.id} lifecycle changed after it was read"
            )

    async def append_event(
        self,
        *,
        turn_id: TurnId,
        event_id: ExecutionEventId,
        attempt_id: TurnAttemptId | None,
        kind: ExecutionEventKind,
        payload: Mapping[str, object],
        occurred_at: datetime,
    ) -> ExecutionEvent:
        statement = select(turns).where(turns.c.id == turn_id).with_for_update()

        result = await self._session.execute(statement)
        record = result.mappings().one_or_none()

        if record is None:
            raise TurnNotFoundError(f"turn {turn_id} was not found")

        turn = turn_from_record(record)

        if turn.status is TurnStatus.RECEIVED:
            raise TurnEventStateError(f"turn {turn.id} has not started")

        if turn.status is TurnStatus.RUNNING:
            allowed_kinds = {
                ExecutionEventKind.TURN_STARTED,
                ExecutionEventKind.TOOL_STARTED,
                ExecutionEventKind.TOOL_COMPLETED,
                ExecutionEventKind.TOOL_FAILED,
                ExecutionEventKind.RESPONSE_DELTA,
            }
        elif turn.status is TurnStatus.COMPLETED:
            allowed_kinds = {
                ExecutionEventKind.TURN_COMPLETED,
            }
        elif turn.status is TurnStatus.FAILED:
            allowed_kinds = {
                ExecutionEventKind.TURN_FAILED,
            }
        else:
            allowed_kinds = set()

        if kind not in allowed_kinds:
            raise TurnEventStateError(
                f"event {kind.value} is not valid for turn status {turn.status.value}"
            )

        updated_turn, allocated_sequence = turn.allocate_event_sequence()

        event = ExecutionEvent(
            id=event_id,
            turn_id=turn_id,
            attempt_id=attempt_id,
            sequence=allocated_sequence,
            kind=kind,
            payload=payload,
            occurred_at=occurred_at,
        )

        if event.occurred_at < turn.created_at:
            raise ValueError("execution event cannot occur before its turn")

        await self._session.execute(
            update(turns)
            .where(turns.c.id == turn_id)
            .values(next_event_sequence=(updated_turn.next_event_sequence))
        )

        await self._session.execute(
            insert(execution_events).values(execution_event_to_record(event))
        )

        return event

    async def list_events(
        self,
        *,
        turn_id: TurnId,
        after_sequence: int,
        limit: int,
    ) -> tuple[ExecutionEvent, ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")

        if limit <= 0:
            raise ValueError("limit must be greater than zero")

        statement = (
            select(execution_events)
            .where(
                execution_events.c.turn_id == turn_id,
                execution_events.c.sequence > after_sequence,
            )
            .order_by(execution_events.c.sequence)
            .limit(limit)
        )

        result = await self._session.execute(statement)

        return tuple(execution_event_from_record(record) for record in result.mappings())


class SqlAlchemyToolInvocationRepository:
    """Persists one bounded logical tool invocation per attempt."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, invocation: ToolInvocation) -> None:
        await self._session.execute(
            insert(tool_invocations).values(tool_invocation_to_record(invocation))
        )

    async def get(self, invocation_id: ToolInvocationId) -> ToolInvocation | None:
        result = await self._session.execute(
            select(tool_invocations).where(tool_invocations.c.id == invocation_id)
        )
        record = result.mappings().one_or_none()
        return None if record is None else tool_invocation_from_record(record)

    async def list_for_turn(self, turn_id: TurnId) -> tuple[ToolInvocation, ...]:
        result = await self._session.execute(
            select(tool_invocations)
            .where(tool_invocations.c.turn_id == turn_id)
            .order_by(tool_invocations.c.invocation_number)
        )
        return tuple(tool_invocation_from_record(record) for record in result.mappings())

    async def update_lifecycle(
        self,
        *,
        previous: ToolInvocation,
        updated: ToolInvocation,
    ) -> None:
        previous_immutable = (
            previous.id,
            previous.turn_id,
            previous.attempt_id,
            previous.invocation_number,
            previous.tool_definition_id,
            previous.tool_version_id,
            previous.arguments,
            previous.idempotency_key,
            previous.authorized_scopes,
            previous.created_at,
        )
        updated_immutable = (
            updated.id,
            updated.turn_id,
            updated.attempt_id,
            updated.invocation_number,
            updated.tool_definition_id,
            updated.tool_version_id,
            updated.arguments,
            updated.idempotency_key,
            updated.authorized_scopes,
            updated.created_at,
        )
        if previous_immutable != updated_immutable:
            raise ValueError("tool invocation lifecycle transition must preserve immutable fields")

        result = await self._session.execute(
            update(tool_invocations)
            .where(
                tool_invocations.c.id == previous.id,
                tool_invocations.c.status == previous.status.value,
                _matches_nullable(tool_invocations.c.started_at, previous.started_at),
                _matches_nullable(tool_invocations.c.completed_at, previous.completed_at),
                _matches_nullable(tool_invocations.c.failure_code, previous.failure_code),
            )
            .values(
                status=updated.status.value,
                started_at=updated.started_at,
                completed_at=updated.completed_at,
                result=(None if updated.result is None else mutable_json_value(updated.result)),
                failure_code=updated.failure_code,
            )
            .returning(tool_invocations.c.id)
        )
        if result.scalar_one_or_none() is None:
            raise ToolInvocationLifecycleConflictError(
                f"tool invocation {previous.id} lifecycle changed after it was read"
            )


class SqlAlchemyToolRegistryRepository:
    """Persists immutable tool registry records and CAS lifecycle state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_definition(self, definition: ToolDefinition) -> None:
        await self._session.execute(
            insert(tool_definitions).values(tool_definition_to_record(definition))
        )

    async def add_definition_if_absent(self, definition: ToolDefinition) -> bool:
        result = await self._session.execute(
            postgresql_insert(tool_definitions)
            .values(tool_definition_to_record(definition))
            .on_conflict_do_nothing(constraint="team_tool_key")
            .returning(tool_definitions.c.id)
        )
        return result.scalar_one_or_none() is not None

    async def get_definition(
        self,
        tool_definition_id: ToolDefinitionId,
    ) -> ToolDefinition | None:
        result = await self._session.execute(
            select(tool_definitions).where(tool_definitions.c.id == tool_definition_id)
        )
        record = result.mappings().one_or_none()
        return None if record is None else tool_definition_from_record(record)

    async def get_definition_by_key(
        self,
        *,
        team_id: TeamId,
        tool_key: str,
    ) -> ToolDefinition | None:
        result = await self._session.execute(
            select(tool_definitions).where(
                tool_definitions.c.team_id == team_id,
                tool_definitions.c.tool_key == tool_key,
            )
        )
        record = result.mappings().one_or_none()
        return None if record is None else tool_definition_from_record(record)

    async def add_next_version(
        self,
        *,
        tool_version_id: ToolVersionId,
        tool_definition_id: ToolDefinitionId,
        manifest: ToolManifest,
        created_at: datetime,
    ) -> ToolVersion:
        definition_result = await self._session.execute(
            select(tool_definitions.c.id)
            .where(tool_definitions.c.id == tool_definition_id)
            .with_for_update()
        )
        if definition_result.scalar_one_or_none() is None:
            raise ToolDefinitionNotFoundError(f"tool definition {tool_definition_id} was not found")

        version_result = await self._session.execute(
            select(func.coalesce(func.max(tool_versions.c.version_number), 0)).where(
                tool_versions.c.tool_definition_id == tool_definition_id
            )
        )
        version = ToolVersion(
            id=tool_version_id,
            tool_definition_id=tool_definition_id,
            version_number=version_result.scalar_one() + 1,
            manifest=manifest,
            content_hash=manifest.content_hash,
            created_at=created_at,
        )
        state = ToolVersionState(
            tool_version_id=version.id,
            status=ToolLifecycleStatus.DRAFT,
            revision=1,
            activated_conformance_run_id=None,
            created_at=created_at,
            updated_at=created_at,
        )
        await self._session.execute(insert(tool_versions).values(tool_version_to_record(version)))
        await self._session.execute(
            insert(tool_version_states).values(tool_version_state_to_record(state))
        )
        return version

    async def get_version(self, tool_version_id: ToolVersionId) -> ToolVersion | None:
        result = await self._session.execute(
            select(tool_versions).where(tool_versions.c.id == tool_version_id)
        )
        record = result.mappings().one_or_none()
        return None if record is None else tool_version_from_record(record)

    async def get_version_state(
        self,
        tool_version_id: ToolVersionId,
    ) -> ToolVersionState | None:
        result = await self._session.execute(
            select(tool_version_states).where(
                tool_version_states.c.tool_version_id == tool_version_id
            )
        )
        record = result.mappings().one_or_none()
        return None if record is None else tool_version_state_from_record(record)

    async def get_version_state_for_update(
        self,
        tool_version_id: ToolVersionId,
    ) -> ToolVersionState | None:
        result = await self._session.execute(
            select(tool_version_states)
            .where(tool_version_states.c.tool_version_id == tool_version_id)
            .with_for_update()
        )
        record = result.mappings().one_or_none()
        return None if record is None else tool_version_state_from_record(record)

    async def update_version_state(
        self,
        *,
        previous: ToolVersionState,
        updated: ToolVersionState,
    ) -> None:
        if previous.tool_version_id != updated.tool_version_id:
            raise ValueError("tool lifecycle transition must preserve identity")
        if updated.revision != previous.revision + 1:
            raise ValueError("tool lifecycle transition must increment revision")

        result = await self._session.execute(
            update(tool_version_states)
            .where(
                tool_version_states.c.tool_version_id == previous.tool_version_id,
                tool_version_states.c.revision == previous.revision,
            )
            .values(tool_version_state_to_record(updated))
            .returning(tool_version_states.c.tool_version_id)
        )
        if result.scalar_one_or_none() is None:
            raise ToolVersionLifecycleConflictError(
                f"tool version {previous.tool_version_id} lifecycle changed after it was read"
            )

    async def add_binding(self, binding: AgentToolBinding) -> None:
        await self._session.execute(
            insert(agent_tool_bindings).values(agent_tool_binding_to_record(binding))
        )

    async def get_binding(
        self,
        binding_id: AgentToolBindingId,
    ) -> AgentToolBinding | None:
        result = await self._session.execute(
            select(agent_tool_bindings).where(agent_tool_bindings.c.id == binding_id)
        )
        record = result.mappings().one_or_none()
        return None if record is None else agent_tool_binding_from_record(record)

    async def list_bindings(
        self,
        agent_version_id: AgentVersionId,
    ) -> tuple[AgentToolBinding, ...]:
        result = await self._session.execute(
            select(agent_tool_bindings)
            .where(agent_tool_bindings.c.agent_version_id == agent_version_id)
            .order_by(agent_tool_bindings.c.tool_definition_id)
        )
        return tuple(agent_tool_binding_from_record(record) for record in result.mappings())

    async def list_eligible_for_agent(
        self,
        *,
        team_id: TeamId,
        agent_version_id: AgentVersionId,
    ) -> tuple[EligibleTool, ...]:
        statement = (
            select(
                tool_definitions.c.id.label("definition_id"),
                tool_definitions.c.team_id.label("definition_team_id"),
                tool_definitions.c.tool_key,
                tool_definitions.c.created_at.label("definition_created_at"),
                tool_versions.c.id.label("version_id"),
                tool_versions.c.tool_definition_id,
                tool_versions.c.version_number,
                tool_versions.c.manifest,
                tool_versions.c.content_hash,
                tool_versions.c.created_at.label("version_created_at"),
            )
            .select_from(
                agent_tool_bindings.join(
                    agent_versions,
                    agent_versions.c.id == agent_tool_bindings.c.agent_version_id,
                )
                .join(
                    agent_definitions,
                    agent_definitions.c.id == agent_versions.c.agent_definition_id,
                )
                .join(
                    tool_versions,
                    tool_versions.c.id == agent_tool_bindings.c.tool_version_id,
                )
                .join(
                    tool_definitions,
                    tool_definitions.c.id == agent_tool_bindings.c.tool_definition_id,
                )
                .join(
                    tool_version_states,
                    tool_version_states.c.tool_version_id == tool_versions.c.id,
                )
                .join(
                    tool_conformance_runs,
                    tool_conformance_runs.c.id
                    == tool_version_states.c.activated_conformance_run_id,
                )
            )
            .where(
                agent_tool_bindings.c.agent_version_id == agent_version_id,
                agent_definitions.c.team_id == team_id,
                tool_definitions.c.team_id == team_id,
                tool_version_states.c.status == ToolLifecycleStatus.ACTIVE.value,
                tool_conformance_runs.c.status == ToolConformanceStatus.PASSED.value,
            )
            .order_by(tool_definitions.c.tool_key, tool_versions.c.version_number)
        )
        result = await self._session.execute(statement)
        eligible: list[EligibleTool] = []
        for record in result.mappings():
            definition = tool_definition_from_record(
                {
                    "id": record["definition_id"],
                    "team_id": record["definition_team_id"],
                    "tool_key": record["tool_key"],
                    "created_at": record["definition_created_at"],
                }
            )
            version = tool_version_from_record(
                {
                    "id": record["version_id"],
                    "tool_definition_id": record["tool_definition_id"],
                    "version_number": record["version_number"],
                    "manifest": record["manifest"],
                    "content_hash": record["content_hash"],
                    "created_at": record["version_created_at"],
                }
            )
            eligible.append(EligibleTool(definition=definition, version=version))
        return tuple(eligible)

    async def add_conformance_run(
        self,
        run: ToolConformanceRun,
        case_results: tuple[ToolConformanceCaseResult, ...],
    ) -> None:
        if not case_results:
            raise ValueError("conformance run requires at least one case result")
        if any(result.run_id != run.id for result in case_results):
            raise ValueError("conformance case result must belong to its run")
        expected_status = (
            ToolConformanceStatus.PASSED
            if all(result.status is ToolConformanceStatus.PASSED for result in case_results)
            else ToolConformanceStatus.FAILED
        )
        if run.status is not expected_status:
            raise ValueError("conformance run status must summarize its case results")

        await self._session.execute(
            insert(tool_conformance_runs).values(tool_conformance_run_to_record(run))
        )
        await self._session.execute(
            insert(tool_conformance_case_results).values(
                [tool_conformance_case_result_to_record(result) for result in case_results]
            )
        )

    async def get_conformance_run(
        self,
        run_id: ToolConformanceRunId,
    ) -> tuple[ToolConformanceRun, tuple[ToolConformanceCaseResult, ...]] | None:
        run_result = await self._session.execute(
            select(tool_conformance_runs).where(tool_conformance_runs.c.id == run_id)
        )
        run_record = run_result.mappings().one_or_none()
        if run_record is None:
            return None

        cases_result = await self._session.execute(
            select(tool_conformance_case_results)
            .where(tool_conformance_case_results.c.run_id == run_id)
            .order_by(tool_conformance_case_results.c.case_key)
        )
        return (
            tool_conformance_run_from_record(run_record),
            tuple(
                tool_conformance_case_result_from_record(record)
                for record in cases_result.mappings()
            ),
        )
