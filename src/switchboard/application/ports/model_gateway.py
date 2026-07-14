"""Provider-independent structured model contracts."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from switchboard.domain.common import require_not_blank
from switchboard.domain.context import ContextItemKind
from switchboard.domain.conversations import MessageRole
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import ToolDefinitionId, ToolVersionId
from switchboard.domain.json_values import (
    JsonObject,
    canonical_json,
    freeze_json_object,
)

MAX_MODEL_CONTEXT_ITEMS = 128
MAX_MODEL_TOOL_CANDIDATES = 32
MAX_MODEL_RESPONSE_CHARS = 32_000
MAX_MODEL_REQUEST_CONTENT_CHARS = 128_000
MAX_TOOL_ARGUMENT_JSON_CHARS = 16_000
MAX_TOOL_RESULT_JSON_CHARS = 64_000


class ModelRequestPhase(StrEnum):
    """Whether the model is choosing an action or producing final output."""

    INITIAL = "initial"
    FINAL = "final"


@dataclass(frozen=True, slots=True)
class ModelContextItem:
    """One normalized bounded context item visible to a model gateway."""

    kind: ContextItemKind
    content: str
    role: MessageRole | None

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise DomainValidationError("content must not be blank")
        if len(self.content) > MAX_MODEL_RESPONSE_CHARS:
            raise DomainValidationError("context item content is too long")
        if self.kind is ContextItemKind.MESSAGE and self.role is None:
            raise DomainValidationError("model message context requires a role")
        if self.kind is ContextItemKind.SUMMARY and self.role is not None:
            raise DomainValidationError("model summary context must not have a role")


@dataclass(frozen=True, slots=True)
class ModelToolDescriptor:
    """Safe exact-version tool contract exposed for structured selection."""

    tool_definition_id: ToolDefinitionId
    tool_version_id: ToolVersionId
    tool_key: str
    display_name: str
    description: str
    input_schema: JsonObject

    def __post_init__(self) -> None:
        tool_key = require_not_blank(self.tool_key, field_name="tool_key")
        display_name = require_not_blank(self.display_name, field_name="display_name")
        description = require_not_blank(self.description, field_name="description")
        if len(tool_key) > 100:
            raise DomainValidationError("tool_key is too long")
        if len(display_name) > 200:
            raise DomainValidationError("display_name is too long")
        if len(description) > 2_000:
            raise DomainValidationError("description is too long")
        object.__setattr__(self, "tool_key", tool_key)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "description", description)
        object.__setattr__(
            self,
            "input_schema",
            freeze_json_object(self.input_schema, field_name="input_schema"),
        )


@dataclass(frozen=True, slots=True)
class ModelToolResult:
    """Normalized tool data supplied to the final model step."""

    tool_version_id: ToolVersionId
    output: JsonObject

    def __post_init__(self) -> None:
        output = freeze_json_object(self.output, field_name="output")
        if len(canonical_json(output)) > MAX_TOOL_RESULT_JSON_CHARS:
            raise DomainValidationError("tool result is too large")
        object.__setattr__(self, "output", output)


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Bounded normalized request independent of any provider SDK."""

    phase: ModelRequestPhase
    context: tuple[ModelContextItem, ...]
    tools: tuple[ModelToolDescriptor, ...] = ()
    tool_result: ModelToolResult | None = None

    def __post_init__(self) -> None:
        context = tuple(self.context)
        tools = tuple(self.tools)
        if not context:
            raise DomainValidationError("model request requires context")
        if len(context) > MAX_MODEL_CONTEXT_ITEMS:
            raise DomainValidationError("model request has too many context items")
        if sum(len(item.content) for item in context) > MAX_MODEL_REQUEST_CONTENT_CHARS:
            raise DomainValidationError("model request context is too large")
        if len(tools) > MAX_MODEL_TOOL_CANDIDATES:
            raise DomainValidationError("model request has too many tool candidates")
        if len({tool.tool_version_id for tool in tools}) != len(tools):
            raise DomainValidationError("model request tool versions must be unique")
        if self.phase is ModelRequestPhase.INITIAL and self.tool_result is not None:
            raise DomainValidationError("initial model request must not contain a tool result")
        if self.phase is ModelRequestPhase.FINAL:
            if self.tool_result is None:
                raise DomainValidationError("final model request requires a tool result")
            if tools:
                raise DomainValidationError("final model request must not expose tools")
        object.__setattr__(self, "context", context)
        object.__setattr__(self, "tools", tools)


@dataclass(frozen=True, slots=True)
class Respond:
    """Structured action containing final user-visible response text."""

    text: str

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise DomainValidationError("text must not be blank")
        if len(self.text) > MAX_MODEL_RESPONSE_CHARS:
            raise DomainValidationError("model response is too long")


@dataclass(frozen=True, slots=True)
class CallTool:
    """Untrusted structured request to invoke one exact tool version."""

    tool_version_id: ToolVersionId
    arguments: JsonObject

    def __post_init__(self) -> None:
        arguments = freeze_json_object(self.arguments, field_name="arguments")
        if len(canonical_json(arguments)) > MAX_TOOL_ARGUMENT_JSON_CHARS:
            raise DomainValidationError("tool arguments are too large")
        object.__setattr__(self, "arguments", arguments)


type ModelAction = Respond | CallTool


class ModelGateway(Protocol):
    """Return one normalized structured action from a bounded request."""

    async def generate(self, request: ModelRequest) -> ModelAction:
        """Generate one action without exposing provider-specific values."""
