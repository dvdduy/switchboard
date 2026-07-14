"""Application workflow for reproducible token-bounded turn context."""

from dataclasses import dataclass

from switchboard.application.errors import (
    AgentDefinitionNotFoundError,
    AgentTeamMismatchError,
    AgentVersionNotFoundError,
    ConversationNotFoundError,
    MessageNotFoundError,
    TurnNotFoundError,
)
from switchboard.application.ports.clock import Clock
from switchboard.application.ports.conversation_summarizer import ConversationSummarizer
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.token_counter import TokenCounter
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.application.services.context_assembler import ContextAssembler, ContextSelection
from switchboard.domain.agents import AgentVersion
from switchboard.domain.common import require_not_blank
from switchboard.domain.context import (
    BuiltContext,
    ContextItemCandidate,
    ContextItemKind,
    ConversationSummary,
)
from switchboard.domain.conversations import Message
from switchboard.domain.identifiers import ConversationSummaryId, TurnId
from switchboard.domain.turns import Turn


@dataclass(frozen=True, slots=True)
class BuildTurnContextCommand:
    """Identity of the immutable turn snapshot to reconstruct."""

    turn_id: TurnId


@dataclass(frozen=True, slots=True)
class _ContextSnapshot:
    turn: Turn
    agent_version: AgentVersion
    input_message_sequence: int
    messages: tuple[Message, ...]


class BuildTurnContext:
    """Load, summarize when necessary, and assemble one turn's context."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        token_counter: TokenCounter,
        summarizer: ConversationSummarizer,
        clock: Clock,
        summary_ids: IdGenerator[ConversationSummaryId],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._token_counter = token_counter
        self._summarizer = summarizer
        self._summarizer_version = require_not_blank(
            summarizer.version,
            field_name="summarizer.version",
        )
        self._clock = clock
        self._summary_ids = summary_ids
        self._assembler = ContextAssembler(token_counter)

    async def execute(
        self,
        command: BuildTurnContextCommand,
    ) -> BuiltContext:
        """Build bounded context without holding transactions over summarization."""

        snapshot = await self._load_snapshot(command.turn_id)
        selection = self._assembler.select(
            conversation_id=snapshot.turn.conversation_id,
            turn_id=snapshot.turn.id,
            agent_version_id=snapshot.agent_version.id,
            input_message_id=snapshot.turn.input_message_id,
            input_message_sequence=snapshot.input_message_sequence,
            policy=snapshot.agent_version.context_policy,
            messages=snapshot.messages,
        )

        if not selection.summary_required:
            return self._assembler.build(selection=selection)

        reusable = await self._load_compatible_summary(selection)
        if reusable is not None:
            return self._assembler.build(selection=selection, summary=reusable)

        summary = await self._create_summary(selection)
        # Validate the complete artifact before opening its write transaction.
        self._assembler.build(selection=selection, summary=summary)

        async with self._unit_of_work_factory() as unit_of_work:
            authoritative = await unit_of_work.summaries.add_if_absent(summary)
            await unit_of_work.commit()

        return self._assembler.build(
            selection=selection,
            summary=authoritative,
        )

    async def _load_snapshot(self, turn_id: TurnId) -> _ContextSnapshot:
        async with self._unit_of_work_factory() as unit_of_work:
            turn = await unit_of_work.turns.get(turn_id)
            if turn is None:
                raise TurnNotFoundError(f"turn {turn_id} was not found")

            conversation = await unit_of_work.conversations.get(turn.conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(
                    f"conversation {turn.conversation_id} was not found"
                )

            agent_version = await unit_of_work.agents.get_version(turn.agent_version_id)
            if agent_version is None:
                raise AgentVersionNotFoundError(
                    f"agent version {turn.agent_version_id} was not found"
                )

            agent_definition = await unit_of_work.agents.get_definition(
                agent_version.agent_definition_id
            )
            if agent_definition is None:
                raise AgentDefinitionNotFoundError(
                    f"agent definition {agent_version.agent_definition_id} was not found"
                )
            if agent_definition.team_id != conversation.team_id:
                raise AgentTeamMismatchError(
                    "turn agent version does not belong to the conversation team"
                )

            input_message = await unit_of_work.conversations.get_message(
                conversation_id=turn.conversation_id,
                message_id=turn.input_message_id,
            )
            if input_message is None:
                raise MessageNotFoundError(f"input message {turn.input_message_id} was not found")

            messages = await unit_of_work.conversations.list_messages_through(
                conversation_id=turn.conversation_id,
                through_sequence=input_message.sequence,
            )

        return _ContextSnapshot(
            turn=turn,
            agent_version=agent_version,
            input_message_sequence=input_message.sequence,
            messages=messages,
        )

    async def _load_compatible_summary(
        self,
        selection: ContextSelection,
    ) -> ConversationSummary | None:
        through_sequence = selection.summary_through_sequence
        if through_sequence is None:
            return None

        async with self._unit_of_work_factory() as unit_of_work:
            summary = await unit_of_work.summaries.get_latest_compatible(
                conversation_id=selection.conversation_id,
                agent_version_id=selection.agent_version_id,
                through_sequence=through_sequence,
                summarizer_version=self._summarizer_version,
                token_counter_version=selection.token_counter_version,
            )

        if summary is None or summary.through_sequence != through_sequence:
            return None
        return summary

    async def _create_summary(
        self,
        selection: ContextSelection,
    ) -> ConversationSummary:
        through_sequence = selection.summary_through_sequence
        if through_sequence is None:
            raise RuntimeError("summary creation requires an omitted prefix")

        content = await self._summarizer.summarize(
            messages=selection.omitted_prefix,
            max_tokens=selection.policy.summary_max_tokens,
        )
        candidate = ContextItemCandidate(
            kind=ContextItemKind.SUMMARY,
            role=None,
            content=content,
        )
        estimated_token_count = self._token_counter.count(candidate)

        return ConversationSummary(
            id=self._summary_ids.new(),
            conversation_id=selection.conversation_id,
            agent_version_id=selection.agent_version_id,
            from_sequence=1,
            through_sequence=through_sequence,
            content=content,
            estimated_token_count=estimated_token_count,
            summarizer_version=self._summarizer_version,
            token_counter_version=selection.token_counter_version,
            created_at=self._clock.now(),
        )
