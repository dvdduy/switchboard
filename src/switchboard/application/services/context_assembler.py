"""Deterministic provider-independent conversation context assembly."""

from dataclasses import dataclass

from switchboard.application.errors import ContextBudgetExceededError
from switchboard.application.ports.token_counter import TokenCounter
from switchboard.domain.common import require_not_blank
from switchboard.domain.context import (
    BuiltContext,
    ContextItem,
    ContextItemCandidate,
    ContextItemKind,
    ContextPolicy,
    ConversationSummary,
    MessageContextSource,
)
from switchboard.domain.conversations import Message
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    AgentVersionId,
    ConversationId,
    MessageId,
    TurnId,
)


@dataclass(frozen=True, slots=True)
class ContextSelection:
    """Counted recent suffix plus the exact older prefix it omits."""

    conversation_id: ConversationId
    turn_id: TurnId
    agent_version_id: AgentVersionId
    input_message_id: MessageId
    input_message_sequence: int
    policy: ContextPolicy
    token_counter_version: str
    omitted_prefix: tuple[Message, ...]
    recent_items: tuple[ContextItem, ...]

    @property
    def summary_required(self) -> bool:
        return bool(self.omitted_prefix)

    @property
    def summary_through_sequence(self) -> int | None:
        if not self.omitted_prefix:
            return None
        return self.omitted_prefix[-1].sequence


class ContextAssembler:
    """Select and assemble bounded context from an immutable message snapshot."""

    def __init__(self, token_counter: TokenCounter) -> None:
        self._token_counter = token_counter
        self._token_counter_version = require_not_blank(
            token_counter.version,
            field_name="token_counter.version",
        )

    def select(
        self,
        *,
        conversation_id: ConversationId,
        turn_id: TurnId,
        agent_version_id: AgentVersionId,
        input_message_id: MessageId,
        input_message_sequence: int,
        policy: ContextPolicy,
        messages: tuple[Message, ...],
    ) -> ContextSelection:
        """Choose the newest contiguous suffix while reserving summary capacity."""

        snapshot = tuple(messages)
        self._validate_snapshot(
            conversation_id=conversation_id,
            input_message_id=input_message_id,
            input_message_sequence=input_message_sequence,
            messages=snapshot,
        )

        mandatory_start = max(0, len(snapshot) - policy.minimum_recent_messages)
        counted = tuple(
            self._message_item(
                message,
                mandatory=index >= mandatory_start,
            )
            for index, message in enumerate(snapshot)
        )
        total_tokens = sum(item.token_count for item in counted)

        if total_tokens <= policy.available_input_tokens:
            return ContextSelection(
                conversation_id=conversation_id,
                turn_id=turn_id,
                agent_version_id=agent_version_id,
                input_message_id=input_message_id,
                input_message_sequence=input_message_sequence,
                policy=policy,
                token_counter_version=self._token_counter_version,
                omitted_prefix=(),
                recent_items=counted,
            )

        mandatory_tokens = sum(item.token_count for item in counted[mandatory_start:])
        if mandatory_tokens > policy.available_input_tokens:
            raise ContextBudgetExceededError(
                available_tokens=policy.available_input_tokens,
                required_tokens=mandatory_tokens,
            )

        required_with_summary = mandatory_tokens + policy.summary_max_tokens
        if required_with_summary > policy.available_input_tokens:
            raise ContextBudgetExceededError(
                available_tokens=policy.available_input_tokens,
                required_tokens=required_with_summary,
            )

        suffix_budget = policy.available_input_tokens - policy.summary_max_tokens
        suffix_start = mandatory_start
        suffix_tokens = mandatory_tokens

        while suffix_start > 0:
            candidate = counted[suffix_start - 1]
            if suffix_tokens + candidate.token_count > suffix_budget:
                break
            suffix_start -= 1
            suffix_tokens += candidate.token_count

        return ContextSelection(
            conversation_id=conversation_id,
            turn_id=turn_id,
            agent_version_id=agent_version_id,
            input_message_id=input_message_id,
            input_message_sequence=input_message_sequence,
            policy=policy,
            token_counter_version=self._token_counter_version,
            omitted_prefix=snapshot[:suffix_start],
            recent_items=counted[suffix_start:],
        )

    def build(
        self,
        *,
        selection: ContextSelection,
        summary: ConversationSummary | None = None,
    ) -> BuiltContext:
        """Build context using a summary for exactly the selected older prefix."""

        if selection.token_counter_version != self._token_counter_version:
            raise DomainValidationError(
                "context selection must use the assembler token counter version"
            )

        if not selection.summary_required:
            if summary is not None:
                raise DomainValidationError("summary must not be supplied when all messages fit")
            items = selection.recent_items
        else:
            if summary is None:
                raise DomainValidationError("summary is required for the omitted message prefix")
            self._validate_summary(selection=selection, summary=summary)
            summary_candidate = ContextItemCandidate(
                kind=ContextItemKind.SUMMARY,
                role=None,
                content=summary.content,
            )
            summary_count = self._token_counter.count(summary_candidate)
            if summary_count != summary.estimated_token_count:
                raise DomainValidationError(
                    "summary estimated token count must match the selected token counter"
                )
            if summary_count > selection.policy.summary_max_tokens:
                raise ContextBudgetExceededError(
                    available_tokens=selection.policy.summary_max_tokens,
                    required_tokens=summary_count,
                )
            items = (
                ContextItem(
                    candidate=summary_candidate,
                    source=summary.source,
                    token_count=summary_count,
                ),
                *selection.recent_items,
            )

        return BuiltContext(
            conversation_id=selection.conversation_id,
            turn_id=selection.turn_id,
            agent_version_id=selection.agent_version_id,
            input_message_id=selection.input_message_id,
            input_message_sequence=selection.input_message_sequence,
            policy=selection.policy,
            token_counter_version=selection.token_counter_version,
            items=items,
        )

    def _message_item(
        self,
        message: Message,
        *,
        mandatory: bool,
    ) -> ContextItem:
        candidate = ContextItemCandidate(
            kind=ContextItemKind.MESSAGE,
            role=message.role,
            content=message.content,
        )
        return ContextItem(
            candidate=candidate,
            source=MessageContextSource(
                message_id=message.id,
                sequence=message.sequence,
            ),
            token_count=self._token_counter.count(candidate),
            mandatory=mandatory,
        )

    @staticmethod
    def _validate_snapshot(
        *,
        conversation_id: ConversationId,
        input_message_id: MessageId,
        input_message_sequence: int,
        messages: tuple[Message, ...],
    ) -> None:
        if not messages:
            raise DomainValidationError("context message snapshot must not be empty")

        for expected_sequence, message in enumerate(messages, start=1):
            if message.conversation_id != conversation_id:
                raise DomainValidationError(
                    "every context message must belong to the selected conversation"
                )
            if message.sequence != expected_sequence:
                raise DomainValidationError(
                    "context messages must be contiguous and ordered from sequence 1"
                )

        current_input = messages[-1]
        if current_input.id != input_message_id or current_input.sequence != input_message_sequence:
            raise DomainValidationError("context snapshot must end at the selected input message")

    @staticmethod
    def _validate_summary(
        *,
        selection: ContextSelection,
        summary: ConversationSummary,
    ) -> None:
        if summary.conversation_id != selection.conversation_id:
            raise DomainValidationError("summary must belong to the selected conversation")
        if summary.agent_version_id != selection.agent_version_id:
            raise DomainValidationError("summary must use the selected agent version")
        if summary.token_counter_version != selection.token_counter_version:
            raise DomainValidationError("summary must use the selected token counter version")
        if summary.from_sequence != 1 or (
            summary.through_sequence != selection.summary_through_sequence
        ):
            raise DomainValidationError("summary must cover exactly the omitted message prefix")
