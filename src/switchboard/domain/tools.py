"""Immutable tool registry contracts and lifecycle entities."""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from hashlib import sha256

from switchboard.domain.common import normalize_utc, require_not_before, require_positive
from switchboard.domain.errors import DomainValidationError, InvalidStateTransition
from switchboard.domain.identifiers import (
    AgentToolBindingId,
    AgentVersionId,
    TeamId,
    ToolConformanceCaseResultId,
    ToolConformanceRunId,
    ToolDefinitionId,
    ToolVersionId,
)
from switchboard.domain.json_values import JsonObject, canonical_json, freeze_json_object

TOOL_MANIFEST_SCHEMA_VERSION = "switchboard.tool-manifest/v1"
JSON_SCHEMA_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,99}$")
_SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9._:-]{0,99}$")
_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,99}$")
_DIAGNOSTIC_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,199}$")


class ToolEffect(StrEnum):
    """Declared security and side-effect class of one tool version."""

    READ_ONLY = "read_only"
    MUTATING = "mutating"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    PRIVILEGED = "privileged"


class IdempotencyMode(StrEnum):
    """Whether invocation requires a stable logical idempotency key."""

    NONE = "none"
    REQUIRED = "required"


class ReconciliationMode(StrEnum):
    """How an ambiguous invocation outcome can be reconciled."""

    NONE = "none"
    BY_IDEMPOTENCY_KEY = "by_idempotency_key"


class ToolLifecycleStatus(StrEnum):
    """Mutable availability state kept separate from manifest content."""

    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class ToolConformanceStatus(StrEnum):
    """Final result of a complete deterministic conformance run or case."""

    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RetryPolicyCandidate:
    """Unvalidated retry declaration received at the application boundary."""

    max_attempts: int
    initial_backoff_ms: int
    retryable_error_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry declaration embedded in an immutable manifest."""

    max_attempts: int
    initial_backoff_ms: int
    retryable_error_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 3:
            raise DomainValidationError("max_attempts must be between 1 and 3")
        if not 0 <= self.initial_backoff_ms <= 5_000:
            raise DomainValidationError("initial_backoff_ms must be between 0 and 5000")

        normalized_codes = tuple(
            sorted({code.strip().lower() for code in self.retryable_error_codes})
        )
        if len(normalized_codes) > 32:
            raise DomainValidationError("retryable_error_codes must contain at most 32 values")
        if any(not _ERROR_CODE_PATTERN.fullmatch(code) for code in normalized_codes):
            raise DomainValidationError("retryable_error_codes contains an invalid code")
        if self.max_attempts > 1 and not normalized_codes:
            raise DomainValidationError("retries require at least one retryable error code")
        object.__setattr__(self, "retryable_error_codes", normalized_codes)


@dataclass(frozen=True, slots=True)
class ToolManifestCandidate:
    """Potential manifest that may contain multiple reportable defects."""

    schema_version: str
    display_name: str
    description: str
    input_schema: JsonObject
    output_schema: JsonObject
    effect: str
    required_scopes: tuple[str, ...]
    timeout_ms: int
    retry_policy: RetryPolicyCandidate
    idempotency: str
    reconciliation: str
    adapter_key: str
    sensitive_input_paths: tuple[str, ...] = ()
    sensitive_output_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolManifest:
    """Validated immutable execution and safety contract for one tool version."""

    schema_version: str
    display_name: str
    description: str
    input_schema: JsonObject
    output_schema: JsonObject
    effect: ToolEffect
    required_scopes: tuple[str, ...]
    timeout_ms: int
    retry_policy: RetryPolicy
    idempotency: IdempotencyMode
    reconciliation: ReconciliationMode
    adapter_key: str
    sensitive_input_paths: tuple[str, ...] = ()
    sensitive_output_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != TOOL_MANIFEST_SCHEMA_VERSION:
            raise DomainValidationError("schema_version is not supported")

        display_name = self.display_name.strip()
        description = self.description.strip()
        adapter_key = self.adapter_key.strip().lower()
        if not display_name or len(display_name) > 200:
            raise DomainValidationError("display_name must contain between 1 and 200 characters")
        if not description or len(description) > 2_000:
            raise DomainValidationError("description must contain between 1 and 2000 characters")
        if not _KEY_PATTERN.fullmatch(adapter_key):
            raise DomainValidationError("adapter_key is invalid")
        if not 1 <= self.timeout_ms <= 30_000:
            raise DomainValidationError("timeout_ms must be between 1 and 30000")

        scopes = tuple(sorted({scope.strip().lower() for scope in self.required_scopes}))
        if not scopes or len(scopes) > 32:
            raise DomainValidationError("required_scopes must contain between 1 and 32 values")
        if any(not _SCOPE_PATTERN.fullmatch(scope) for scope in scopes):
            raise DomainValidationError("required_scopes contains an invalid scope")

        if self.effect is ToolEffect.READ_ONLY:
            if self.reconciliation is not ReconciliationMode.NONE:
                raise DomainValidationError("read-only tools must not declare reconciliation")
        elif (
            self.idempotency is not IdempotencyMode.REQUIRED
            or self.reconciliation is not ReconciliationMode.BY_IDEMPOTENCY_KEY
        ):
            raise DomainValidationError(
                "non-read-only tools require idempotency and reconciliation"
            )

        for schema_name, schema in (
            ("input_schema", self.input_schema),
            ("output_schema", self.output_schema),
        ):
            if schema.get("$schema") != JSON_SCHEMA_DRAFT_2020_12:
                raise DomainValidationError(f"{schema_name} uses an unsupported draft")
            if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
                raise DomainValidationError(f"{schema_name} must be a closed object schema")

        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "adapter_key", adapter_key)
        object.__setattr__(self, "required_scopes", scopes)
        object.__setattr__(
            self,
            "sensitive_input_paths",
            tuple(sorted(set(self.sensitive_input_paths))),
        )
        object.__setattr__(
            self,
            "sensitive_output_paths",
            tuple(sorted(set(self.sensitive_output_paths))),
        )
        object.__setattr__(
            self,
            "input_schema",
            freeze_json_object(self.input_schema, field_name="input_schema"),
        )
        object.__setattr__(
            self,
            "output_schema",
            freeze_json_object(self.output_schema, field_name="output_schema"),
        )

    @property
    def content_hash(self) -> str:
        """Return the deterministic SHA-256 digest of canonical manifest content."""

        content = {
            "schema_version": self.schema_version,
            "display_name": self.display_name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "effect": self.effect.value,
            "required_scopes": self.required_scopes,
            "timeout_ms": self.timeout_ms,
            "retry_policy": {
                "max_attempts": self.retry_policy.max_attempts,
                "initial_backoff_ms": self.retry_policy.initial_backoff_ms,
                "retryable_error_codes": self.retry_policy.retryable_error_codes,
            },
            "idempotency": self.idempotency.value,
            "reconciliation": self.reconciliation.value,
            "adapter_key": self.adapter_key,
            "sensitive_input_paths": self.sensitive_input_paths,
            "sensitive_output_paths": self.sensitive_output_paths,
        }
        return sha256(canonical_json(content).encode()).hexdigest()


DiagnosticPath = tuple[str | int, ...]


@dataclass(frozen=True, slots=True)
class ManifestDiagnostic:
    """Safe deterministic explanation of one rejected manifest field."""

    code: str
    path: DiagnosticPath
    message: str

    def __post_init__(self) -> None:
        code = self.code.strip().lower()
        message = self.message.strip()
        if not _DIAGNOSTIC_CODE_PATTERN.fullmatch(code):
            raise DomainValidationError("diagnostic code is invalid")
        if not message or len(message) > 500 or "\n" in message or "\r" in message:
            raise DomainValidationError("diagnostic message is invalid")
        if any(not isinstance(segment, (str, int)) for segment in self.path):
            raise DomainValidationError("diagnostic path contains an invalid segment")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "path", tuple(self.path))
        object.__setattr__(self, "message", message)


@dataclass(frozen=True, slots=True)
class ManifestValidationResult:
    """Either one valid manifest or one or more safe diagnostics."""

    manifest: ToolManifest | None
    diagnostics: tuple[ManifestDiagnostic, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if self.manifest is None and not self.diagnostics:
            raise DomainValidationError("invalid manifest result requires diagnostics")
        if self.manifest is not None and self.diagnostics:
            raise DomainValidationError("valid manifest result must not contain diagnostics")

    @property
    def is_valid(self) -> bool:
        return self.manifest is not None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Stable team-owned identity for one tool capability."""

    id: ToolDefinitionId
    team_id: TeamId
    tool_key: str
    created_at: datetime

    def __post_init__(self) -> None:
        key = self.tool_key.strip().lower()
        if not _KEY_PATTERN.fullmatch(key):
            raise DomainValidationError("tool_key is invalid")
        object.__setattr__(self, "tool_key", key)
        object.__setattr__(
            self,
            "created_at",
            normalize_utc(self.created_at, field_name="created_at"),
        )


@dataclass(frozen=True, slots=True)
class ToolVersion:
    """Immutable version of one validated tool manifest."""

    id: ToolVersionId
    tool_definition_id: ToolDefinitionId
    version_number: int
    manifest: ToolManifest
    content_hash: str
    created_at: datetime

    def __post_init__(self) -> None:
        require_positive(self.version_number, field_name="version_number")
        if self.content_hash != self.manifest.content_hash:
            raise DomainValidationError("content_hash must match canonical manifest content")
        object.__setattr__(
            self,
            "created_at",
            normalize_utc(self.created_at, field_name="created_at"),
        )


@dataclass(frozen=True, slots=True)
class ToolVersionState:
    """Compare-and-set lifecycle state separate from immutable version content."""

    tool_version_id: ToolVersionId
    status: ToolLifecycleStatus
    revision: int
    activated_conformance_run_id: ToolConformanceRunId | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        require_positive(self.revision, field_name="revision")
        created_at = normalize_utc(self.created_at, field_name="created_at")
        updated_at = normalize_utc(self.updated_at, field_name="updated_at")
        require_not_before(
            updated_at,
            minimum=created_at,
            field_name="updated_at",
            minimum_field_name="created_at",
        )
        if self.status is ToolLifecycleStatus.DRAFT and self.activated_conformance_run_id:
            raise DomainValidationError("draft tool version must not have an activation run")
        if self.status in {ToolLifecycleStatus.ACTIVE, ToolLifecycleStatus.DEPRECATED} and (
            self.activated_conformance_run_id is None
        ):
            raise DomainValidationError("active history requires an activation run")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)

    def activate(
        self,
        *,
        conformance_run_id: ToolConformanceRunId,
        at: datetime,
    ) -> "ToolVersionState":
        if self.status is not ToolLifecycleStatus.DRAFT:
            raise InvalidStateTransition("only a draft tool version can be activated")
        return self._transition(
            status=ToolLifecycleStatus.ACTIVE,
            at=at,
            activated_conformance_run_id=conformance_run_id,
        )

    def deprecate(self, *, at: datetime) -> "ToolVersionState":
        if self.status is not ToolLifecycleStatus.ACTIVE:
            raise InvalidStateTransition("only an active tool version can be deprecated")
        return self._transition(status=ToolLifecycleStatus.DEPRECATED, at=at)

    def disable(self, *, at: datetime) -> "ToolVersionState":
        if self.status is ToolLifecycleStatus.DISABLED:
            raise InvalidStateTransition("disabled tool version is terminal")
        return self._transition(status=ToolLifecycleStatus.DISABLED, at=at)

    def _transition(
        self,
        *,
        status: ToolLifecycleStatus,
        at: datetime,
        activated_conformance_run_id: ToolConformanceRunId | None = None,
    ) -> "ToolVersionState":
        normalized_at = normalize_utc(at, field_name="at")
        require_not_before(
            normalized_at,
            minimum=self.updated_at,
            field_name="at",
            minimum_field_name="updated_at",
        )
        return replace(
            self,
            status=status,
            revision=self.revision + 1,
            activated_conformance_run_id=(
                activated_conformance_run_id or self.activated_conformance_run_id
            ),
            updated_at=normalized_at,
        )


@dataclass(frozen=True, slots=True)
class AgentToolBinding:
    """Immutable binding of one agent version to one exact tool version."""

    id: AgentToolBindingId
    agent_version_id: AgentVersionId
    tool_definition_id: ToolDefinitionId
    tool_version_id: ToolVersionId
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "created_at",
            normalize_utc(self.created_at, field_name="created_at"),
        )


@dataclass(frozen=True, slots=True)
class EligibleTool:
    """Active exact-version manifest eligible for one pinned agent version."""

    definition: ToolDefinition
    version: ToolVersion

    def __post_init__(self) -> None:
        if self.version.tool_definition_id != self.definition.id:
            raise DomainValidationError("eligible tool version must belong to its definition")


@dataclass(frozen=True, slots=True)
class ToolConformanceRun:
    """Immutable summary of one complete conformance run."""

    id: ToolConformanceRunId
    tool_version_id: ToolVersionId
    status: ToolConformanceStatus
    started_at: datetime
    completed_at: datetime

    def __post_init__(self) -> None:
        started_at = normalize_utc(self.started_at, field_name="started_at")
        completed_at = normalize_utc(self.completed_at, field_name="completed_at")
        require_not_before(
            completed_at,
            minimum=started_at,
            field_name="completed_at",
            minimum_field_name="started_at",
        )
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "completed_at", completed_at)


@dataclass(frozen=True, slots=True)
class ToolConformanceCaseResult:
    """Immutable safe result for one platform-owned conformance case."""

    id: ToolConformanceCaseResultId
    run_id: ToolConformanceRunId
    case_key: str
    status: ToolConformanceStatus
    duration_ms: int
    diagnostic_code: str | None

    def __post_init__(self) -> None:
        case_key = self.case_key.strip().lower()
        if not _KEY_PATTERN.fullmatch(case_key):
            raise DomainValidationError("case_key is invalid")
        if not 0 <= self.duration_ms <= 300_000:
            raise DomainValidationError("duration_ms must be between 0 and 300000")
        diagnostic_code = (
            None if self.diagnostic_code is None else self.diagnostic_code.strip().lower()
        )
        if self.status is ToolConformanceStatus.PASSED and diagnostic_code is not None:
            raise DomainValidationError("passed conformance case must not have a diagnostic")
        if self.status is ToolConformanceStatus.FAILED and (
            diagnostic_code is None or not _DIAGNOSTIC_CODE_PATTERN.fullmatch(diagnostic_code)
        ):
            raise DomainValidationError("failed conformance case requires a diagnostic code")
        object.__setattr__(self, "case_key", case_key)
        object.__setattr__(self, "diagnostic_code", diagnostic_code)
