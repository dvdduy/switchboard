"""Provider-independent contracts for installed tool adapters."""

import re
from dataclasses import dataclass
from typing import Protocol

from switchboard.domain.errors import DomainValidationError
from switchboard.domain.json_values import JsonObject, freeze_json_object

_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,99}$")


@dataclass(frozen=True, slots=True)
class ToolInvocationRequest:
    """Normalized invocation passed to one preinstalled adapter."""

    arguments: JsonObject
    idempotency_key: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "arguments",
            freeze_json_object(self.arguments, field_name="arguments"),
        )
        if self.idempotency_key is not None and not self.idempotency_key.strip():
            raise DomainValidationError("idempotency_key must not be blank")


@dataclass(frozen=True, slots=True)
class ToolInvocationSuccess:
    """Normalized JSON-compatible adapter output."""

    output: JsonObject

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "output",
            freeze_json_object(self.output, field_name="output"),
        )


@dataclass(frozen=True, slots=True)
class ToolInvocationFailure:
    """Declared adapter failure without provider messages or rejected values."""

    error_code: str
    retryable: bool

    def __post_init__(self) -> None:
        error_code = self.error_code.strip().lower()
        if not _ERROR_CODE_PATTERN.fullmatch(error_code):
            raise DomainValidationError("error_code is invalid")
        object.__setattr__(self, "error_code", error_code)


ToolInvocationResult = ToolInvocationSuccess | ToolInvocationFailure


@dataclass(frozen=True, slots=True)
class ToolReconciliationResult:
    """Normalized lookup of an earlier idempotent operation."""

    found: bool
    output: JsonObject | None

    def __post_init__(self) -> None:
        if self.found != (self.output is not None):
            raise DomainValidationError("reconciliation output must match found status")
        if self.output is not None:
            object.__setattr__(
                self,
                "output",
                freeze_json_object(self.output, field_name="output"),
            )


class ToolAdapter(Protocol):
    """Invoke and reconcile one normalized installed tool implementation."""

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        """Execute one bounded normalized invocation."""

    async def reconcile(self, idempotency_key: str) -> ToolReconciliationResult:
        """Look up an ambiguous operation by its stable logical key."""


class ToolAdapterResolver(Protocol):
    """Resolve immutable local adapter keys without dynamic code loading."""

    def resolve(self, adapter_key: str) -> ToolAdapter | None:
        """Return one preinstalled adapter, if configured."""
