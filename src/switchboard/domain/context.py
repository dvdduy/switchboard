"""Provider-independent context budget and provenance value objects."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from switchboard.domain.common import normalize_utc, require_not_blank, require_positive
from switchboard.domain.conversations import MessageRole
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    AgentVersionId,
    ConversationId,
    ConversationSummaryId,
    MessageId,
    TurnId,
)


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Immutable capacity policy pinned to an agent version."""

    model_window_tokens: int
    reserved_output_tokens: int
    fixed_overhead_tokens: int
    summary_max_tokens: int
    minimum_recent_messages: int

    def __post_init__(self) -> None:
        for field_name in (
            "model_window_tokens",
            "reserved_output_tokens",
            "fixed_overhead_tokens",
            "summary_max_tokens",
            "minimum_recent_messages",
        ):
            require_positive(
                getattr(self, field_name),
                field_name=field_name,
            )

        if self.available_input_tokens <= 0:
            raise DomainValidationError(
                "reserved output plus fixed overhead must leave conversation input capacity"
            )

        if self.summary_max_tokens >= self.available_input_tokens:
            raise DomainValidationError(
                "summary_max_tokens must be smaller than available input tokens"
            )

    @property
    def available_input_tokens(self) -> int:
        """Return capacity available to summaries and conversation messages."""

        return self.model_window_tokens - self.reserved_output_tokens - self.fixed_overhead_tokens


class ContextItemKind(StrEnum):
    """Stable semantic kind of one model-context item."""

    MESSAGE = "message"
    SUMMARY = "summary"


@dataclass(frozen=True, slots=True)
class ContextItemCandidate:
    """Uncounted content passed through a token-counter port."""

    kind: ContextItemKind
    content: str
    role: MessageRole | None

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise DomainValidationError("context item content must not be blank")

        if self.kind is ContextItemKind.MESSAGE and self.role is None:
            raise DomainValidationError("message context item must have a role")

        if self.kind is ContextItemKind.SUMMARY and self.role is not None:
            raise DomainValidationError("summary context item must not have a message role")


@dataclass(frozen=True, slots=True)
class MessageContextSource:
    """Provenance for one immutable conversation message."""

    message_id: MessageId
    sequence: int

    def __post_init__(self) -> None:
        require_positive(self.sequence, field_name="sequence")


@dataclass(frozen=True, slots=True)
class SummaryContextSource:
    """Coverage and strategy provenance for one derived summary."""

    agent_version_id: AgentVersionId
    from_sequence: int
    through_sequence: int
    summarizer_version: str
    token_counter_version: str

    def __post_init__(self) -> None:
        require_positive(self.from_sequence, field_name="from_sequence")
        require_positive(self.through_sequence, field_name="through_sequence")

        if self.from_sequence != 1:
            raise DomainValidationError("summary coverage must start at sequence 1")

        if self.through_sequence < self.from_sequence:
            raise DomainValidationError(
                "through_sequence must be greater than or equal to from_sequence"
            )

        object.__setattr__(
            self,
            "summarizer_version",
            require_not_blank(
                self.summarizer_version,
                field_name="summarizer_version",
            ),
        )
        object.__setattr__(
            self,
            "token_counter_version",
            require_not_blank(
                self.token_counter_version,
                field_name="token_counter_version",
            ),
        )


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    """Immutable summary of a contiguous conversation prefix."""

    id: ConversationSummaryId
    conversation_id: ConversationId
    agent_version_id: AgentVersionId
    from_sequence: int
    through_sequence: int
    content: str
    estimated_token_count: int
    summarizer_version: str
    token_counter_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        source = SummaryContextSource(
            agent_version_id=self.agent_version_id,
            from_sequence=self.from_sequence,
            through_sequence=self.through_sequence,
            summarizer_version=self.summarizer_version,
            token_counter_version=self.token_counter_version,
        )
        object.__setattr__(self, "content", require_not_blank(self.content, field_name="content"))
        require_positive(
            self.estimated_token_count,
            field_name="estimated_token_count",
        )
        object.__setattr__(self, "summarizer_version", source.summarizer_version)
        object.__setattr__(self, "token_counter_version", source.token_counter_version)
        object.__setattr__(
            self,
            "created_at",
            normalize_utc(self.created_at, field_name="created_at"),
        )

    @property
    def source(self) -> SummaryContextSource:
        """Return the persisted summary's context provenance."""

        return SummaryContextSource(
            agent_version_id=self.agent_version_id,
            from_sequence=self.from_sequence,
            through_sequence=self.through_sequence,
            summarizer_version=self.summarizer_version,
            token_counter_version=self.token_counter_version,
        )


type ContextSource = MessageContextSource | SummaryContextSource


@dataclass(frozen=True, slots=True)
class ContextItem:
    """Counted context content with immutable source provenance."""

    candidate: ContextItemCandidate
    source: ContextSource
    token_count: int
    mandatory: bool = False

    def __post_init__(self) -> None:
        require_positive(self.token_count, field_name="token_count")

        if self.candidate.kind is ContextItemKind.MESSAGE and not isinstance(
            self.source,
            MessageContextSource,
        ):
            raise DomainValidationError("message context item must reference a message source")

        if self.candidate.kind is ContextItemKind.SUMMARY and not isinstance(
            self.source,
            SummaryContextSource,
        ):
            raise DomainValidationError("summary context item must reference a summary source")

    @property
    def kind(self) -> ContextItemKind:
        return self.candidate.kind

    @property
    def content(self) -> str:
        return self.candidate.content

    @property
    def role(self) -> MessageRole | None:
        return self.candidate.role


@dataclass(frozen=True, slots=True)
class BuiltContext:
    """Bounded ordered context plus complete accounting and cutoff metadata."""

    conversation_id: ConversationId
    turn_id: TurnId
    agent_version_id: AgentVersionId
    input_message_id: MessageId
    input_message_sequence: int
    policy: ContextPolicy
    token_counter_version: str
    items: tuple[ContextItem, ...]

    def __post_init__(self) -> None:
        require_positive(
            self.input_message_sequence,
            field_name="input_message_sequence",
        )
        object.__setattr__(
            self,
            "token_counter_version",
            require_not_blank(
                self.token_counter_version,
                field_name="token_counter_version",
            ),
        )
        object.__setattr__(self, "items", tuple(self.items))

        if not self.items:
            raise DomainValidationError("built context must contain at least one item")

        if self.used_input_tokens > self.policy.available_input_tokens:
            raise DomainValidationError("built context exceeds available input tokens")

        current_input_count = 0

        for item in self.items:
            if isinstance(item.source, MessageContextSource):
                if item.source.sequence > self.input_message_sequence:
                    raise DomainValidationError(
                        "context message sequence must not exceed the input-message cutoff"
                    )

                if (
                    item.source.message_id == self.input_message_id
                    and item.source.sequence == self.input_message_sequence
                ):
                    current_input_count += 1

                    if not item.mandatory:
                        raise DomainValidationError("current input context item must be mandatory")

            else:
                if item.source.through_sequence > self.input_message_sequence:
                    raise DomainValidationError(
                        "summary coverage must not exceed the input-message cutoff"
                    )

                if item.source.agent_version_id != self.agent_version_id:
                    raise DomainValidationError(
                        "summary context source must use the built context agent version"
                    )

                if item.source.token_counter_version != self.token_counter_version:
                    raise DomainValidationError(
                        "summary context source must use the built context token counter"
                    )

        if current_input_count != 1:
            raise DomainValidationError("built context must contain its current input exactly once")

    @property
    def used_input_tokens(self) -> int:
        """Return tokens consumed by emitted conversation context items."""

        return sum(item.token_count for item in self.items)

    @property
    def remaining_input_tokens(self) -> int:
        """Return unused conversation-input capacity."""

        return self.policy.available_input_tokens - self.used_input_tokens
