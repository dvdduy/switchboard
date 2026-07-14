"""Port for JSON Schema syntax and instance validation."""

from dataclasses import dataclass
from typing import Protocol

from switchboard.domain.json_values import JsonObject
from switchboard.domain.tools import DiagnosticPath


@dataclass(frozen=True, slots=True)
class JsonSchemaIssue:
    """Safe schema-library-independent validation issue."""

    path: DiagnosticPath
    message: str


class JsonSchemaValidator(Protocol):
    """Validate Draft 2020-12 schemas and JSON-compatible instances."""

    def validate_schema(self, schema: JsonObject) -> tuple[JsonSchemaIssue, ...]:
        """Return safe issues describing an invalid schema."""

    def validate_instance(
        self,
        *,
        instance: object,
        schema: JsonObject,
    ) -> tuple[JsonSchemaIssue, ...]:
        """Return safe issues without including rejected instance values."""
