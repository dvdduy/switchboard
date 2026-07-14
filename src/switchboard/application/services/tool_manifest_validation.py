"""Deterministic validation of untrusted tool manifest candidates."""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from switchboard.application.ports.json_schema import JsonSchemaValidator
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.json_values import (
    JsonObject,
    canonical_json,
    freeze_json_object,
)
from switchboard.domain.tools import (
    JSON_SCHEMA_DRAFT_2020_12,
    TOOL_MANIFEST_SCHEMA_VERSION,
    DiagnosticPath,
    IdempotencyMode,
    ManifestDiagnostic,
    ManifestValidationResult,
    ReconciliationMode,
    RetryPolicy,
    ToolEffect,
    ToolManifest,
    ToolManifestCandidate,
)

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,99}$")
_SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9._:-]{0,99}$")
_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,99}$")
_MAX_SCHEMA_BYTES = 65_536
_MAX_SCHEMA_DEPTH = 20
_MAX_SCHEMA_NODES = 2_000
_MAX_DIAGNOSTICS = 100


@dataclass(frozen=True, slots=True)
class _SchemaMetrics:
    depth: int
    nodes: int
    forbidden_reference_paths: tuple[DiagnosticPath, ...]


class ToolManifestValidator:
    """Produce one immutable manifest or ordered safe diagnostics."""

    def __init__(self, schema_validator: JsonSchemaValidator) -> None:
        self._schema_validator = schema_validator

    def validate(self, candidate: ToolManifestCandidate) -> ManifestValidationResult:
        diagnostics: list[ManifestDiagnostic] = []

        schema_version = candidate.schema_version.strip()
        display_name = candidate.display_name.strip()
        description = candidate.description.strip()
        adapter_key = candidate.adapter_key.strip().lower()

        if schema_version != TOOL_MANIFEST_SCHEMA_VERSION:
            diagnostics.append(
                self._diagnostic(
                    "manifest.identity.unsupported_version",
                    ("schema_version",),
                    "manifest schema version is not supported",
                )
            )
        if not 1 <= len(display_name) <= 200:
            diagnostics.append(
                self._diagnostic(
                    "manifest.bounds.exceeded",
                    ("display_name",),
                    "display name must contain between 1 and 200 characters",
                )
            )
        if not 1 <= len(description) <= 2_000:
            diagnostics.append(
                self._diagnostic(
                    "manifest.bounds.exceeded",
                    ("description",),
                    "description must contain between 1 and 2000 characters",
                )
            )
        if not _KEY_PATTERN.fullmatch(adapter_key):
            diagnostics.append(
                self._diagnostic(
                    "manifest.adapter.invalid_key",
                    ("adapter_key",),
                    "adapter key must be a normalized local registry key",
                )
            )

        effect = self._parse_effect(candidate.effect, diagnostics)
        idempotency = self._parse_idempotency(candidate.idempotency, diagnostics)
        reconciliation = self._parse_reconciliation(candidate.reconciliation, diagnostics)

        scopes = tuple(sorted({scope.strip().lower() for scope in candidate.required_scopes}))
        if not 1 <= len(scopes) <= 32:
            diagnostics.append(
                self._diagnostic(
                    "manifest.scope.invalid",
                    ("required_scopes",),
                    "required scopes must contain between 1 and 32 values",
                )
            )
        for index, scope in enumerate(scopes):
            if not _SCOPE_PATTERN.fullmatch(scope):
                diagnostics.append(
                    self._diagnostic(
                        "manifest.scope.invalid",
                        ("required_scopes", index),
                        "scope must use the normalized scope syntax",
                    )
                )

        if isinstance(candidate.timeout_ms, bool) or not 1 <= candidate.timeout_ms <= 30_000:
            diagnostics.append(
                self._diagnostic(
                    "manifest.bounds.exceeded",
                    ("timeout_ms",),
                    "timeout must be between 1 and 30000 milliseconds",
                )
            )

        retry_policy = self._validate_retry_policy(candidate, diagnostics)
        self._validate_effect_matrix(
            effect=effect,
            idempotency=idempotency,
            reconciliation=reconciliation,
            diagnostics=diagnostics,
        )

        input_schema = self._validate_schema(
            candidate.input_schema,
            field_name="input_schema",
            diagnostics=diagnostics,
        )
        output_schema = self._validate_schema(
            candidate.output_schema,
            field_name="output_schema",
            diagnostics=diagnostics,
        )

        input_paths = self._validate_redaction_paths(
            paths=candidate.sensitive_input_paths,
            schema=input_schema,
            field_name="sensitive_input_paths",
            diagnostics=diagnostics,
        )
        output_paths = self._validate_redaction_paths(
            paths=candidate.sensitive_output_paths,
            schema=output_schema,
            field_name="sensitive_output_paths",
            diagnostics=diagnostics,
        )

        ordered = self._ordered_diagnostics(diagnostics)
        if ordered:
            return ManifestValidationResult(manifest=None, diagnostics=ordered)

        if (
            effect is None
            or idempotency is None
            or reconciliation is None
            or retry_policy is None
            or input_schema is None
            or output_schema is None
        ):
            raise RuntimeError("manifest validation reached an inconsistent valid state")

        try:
            manifest = ToolManifest(
                schema_version=schema_version,
                display_name=display_name,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema,
                effect=effect,
                required_scopes=scopes,
                timeout_ms=candidate.timeout_ms,
                retry_policy=retry_policy,
                idempotency=idempotency,
                reconciliation=reconciliation,
                adapter_key=adapter_key,
                sensitive_input_paths=input_paths,
                sensitive_output_paths=output_paths,
            )
        except DomainValidationError as error:
            raise RuntimeError("validator failed to report a domain invariant") from error

        return ManifestValidationResult(manifest=manifest, diagnostics=())

    def _validate_retry_policy(
        self,
        candidate: ToolManifestCandidate,
        diagnostics: list[ManifestDiagnostic],
    ) -> RetryPolicy | None:
        retry = candidate.retry_policy
        valid = True
        if isinstance(retry.max_attempts, bool) or not 1 <= retry.max_attempts <= 3:
            valid = False
            diagnostics.append(
                self._diagnostic(
                    "manifest.retry.invalid",
                    ("retry_policy", "max_attempts"),
                    "retry attempts must be between 1 and 3",
                )
            )
        if isinstance(retry.initial_backoff_ms, bool) or not 0 <= retry.initial_backoff_ms <= 5_000:
            valid = False
            diagnostics.append(
                self._diagnostic(
                    "manifest.retry.invalid",
                    ("retry_policy", "initial_backoff_ms"),
                    "initial backoff must be between 0 and 5000 milliseconds",
                )
            )

        codes = tuple(sorted({code.strip().lower() for code in retry.retryable_error_codes}))
        if len(codes) > 32:
            valid = False
            diagnostics.append(
                self._diagnostic(
                    "manifest.retry.invalid",
                    ("retry_policy", "retryable_error_codes"),
                    "retryable errors must contain at most 32 values",
                )
            )
        for index, code in enumerate(codes):
            if not _ERROR_CODE_PATTERN.fullmatch(code):
                valid = False
                diagnostics.append(
                    self._diagnostic(
                        "manifest.retry.invalid",
                        ("retry_policy", "retryable_error_codes", index),
                        "retryable error must use the normalized error-code syntax",
                    )
                )
        if retry.max_attempts > 1 and not codes:
            valid = False
            diagnostics.append(
                self._diagnostic(
                    "manifest.retry.invalid",
                    ("retry_policy", "retryable_error_codes"),
                    "a retrying policy requires a declared transient error",
                )
            )
        if not valid:
            return None
        return RetryPolicy(
            max_attempts=retry.max_attempts,
            initial_backoff_ms=retry.initial_backoff_ms,
            retryable_error_codes=codes,
        )

    def _validate_schema(
        self,
        schema: JsonObject,
        *,
        field_name: str,
        diagnostics: list[ManifestDiagnostic],
    ) -> JsonObject | None:
        path = (field_name,)
        try:
            frozen = freeze_json_object(schema, field_name=field_name)
            encoded_size = len(canonical_json(frozen).encode())
        except (DomainValidationError, RecursionError, TypeError, ValueError):
            diagnostics.append(
                self._diagnostic(
                    "manifest.schema.invalid",
                    path,
                    "schema must contain only finite JSON-compatible values",
                )
            )
            return None

        within_bounds = encoded_size <= _MAX_SCHEMA_BYTES
        if not within_bounds:
            diagnostics.append(
                self._diagnostic(
                    "manifest.bounds.exceeded",
                    path,
                    "schema exceeds the maximum canonical size",
                )
            )

        metrics = self._schema_metrics(frozen)
        if metrics.depth > _MAX_SCHEMA_DEPTH or metrics.nodes > _MAX_SCHEMA_NODES:
            within_bounds = False
            diagnostics.append(
                self._diagnostic(
                    "manifest.bounds.exceeded",
                    path,
                    "schema exceeds the maximum depth or container count",
                )
            )
        diagnostics.extend(
            self._diagnostic(
                "manifest.schema.reference_forbidden",
                (field_name, *reference_path),
                "schema references are not supported in Phase 1",
            )
            for reference_path in metrics.forbidden_reference_paths
        )

        if frozen.get("$schema") != JSON_SCHEMA_DRAFT_2020_12:
            diagnostics.append(
                self._diagnostic(
                    "manifest.schema.unsupported_draft",
                    (field_name, "$schema"),
                    "schema must declare Draft 2020-12",
                )
            )
        if frozen.get("type") != "object" or frozen.get("additionalProperties") is not False:
            diagnostics.append(
                self._diagnostic(
                    "manifest.schema.invalid",
                    path,
                    "schema root must be a closed object",
                )
            )

        if within_bounds:
            diagnostics.extend(
                self._diagnostic(
                    "manifest.schema.invalid",
                    (field_name, *issue.path),
                    issue.message,
                )
                for issue in self._schema_validator.validate_schema(frozen)
            )
        return frozen

    def _validate_redaction_paths(
        self,
        *,
        paths: tuple[str, ...],
        schema: JsonObject | None,
        field_name: str,
        diagnostics: list[ManifestDiagnostic],
    ) -> tuple[str, ...]:
        normalized = tuple(sorted(set(paths)))
        if len(normalized) > 64:
            diagnostics.append(
                self._diagnostic(
                    "manifest.bounds.exceeded",
                    (field_name,),
                    "redaction paths must contain at most 64 values",
                )
            )

        decoded: dict[str, tuple[str, ...]] = {}
        for index, pointer in enumerate(normalized):
            tokens = self._decode_json_pointer(pointer)
            if len(pointer) > 512 or tokens is None or not tokens:
                diagnostics.append(
                    self._diagnostic(
                        "manifest.redaction.invalid_path",
                        (field_name, index),
                        "redaction path must be a bounded non-root JSON Pointer",
                    )
                )
            else:
                decoded[pointer] = tokens

        if schema is None:
            return normalized

        sensitive_paths = self._sensitive_schema_paths(schema)
        declared_paths = set(decoded.values())
        for index, pointer in enumerate(normalized):
            tokens = decoded.get(pointer)
            if tokens is not None and tokens not in sensitive_paths:
                diagnostics.append(
                    self._diagnostic(
                        "manifest.redaction.invalid_path",
                        (field_name, index),
                        "redaction path must reference a property marked sensitive",
                    )
                )
        if sensitive_paths - declared_paths:
            diagnostics.append(
                self._diagnostic(
                    "manifest.redaction.invalid_path",
                    (field_name,),
                    "every schema property marked sensitive requires a redaction path",
                )
            )
        return normalized

    @staticmethod
    def _schema_metrics(schema: JsonObject) -> _SchemaMetrics:
        forbidden: list[DiagnosticPath] = []

        def visit(value: object, *, depth: int, path: DiagnosticPath) -> tuple[int, int]:
            if isinstance(value, Mapping):
                max_depth = depth
                nodes = 1
                for key, nested in value.items():
                    if key in {"$ref", "$dynamicRef", "$recursiveRef"}:
                        forbidden.append((*path, key))
                    nested_depth, nested_nodes = visit(
                        nested,
                        depth=depth + 1,
                        path=(*path, key),
                    )
                    max_depth = max(max_depth, nested_depth)
                    nodes += nested_nodes
                return max_depth, nodes
            if isinstance(value, tuple):
                max_depth = depth
                nodes = 1
                for index, nested in enumerate(value):
                    nested_depth, nested_nodes = visit(
                        nested,
                        depth=depth + 1,
                        path=(*path, index),
                    )
                    max_depth = max(max_depth, nested_depth)
                    nodes += nested_nodes
                return max_depth, nodes
            return depth, 0

        max_depth, node_count = visit(schema, depth=1, path=())
        return _SchemaMetrics(max_depth, node_count, tuple(forbidden))

    @staticmethod
    def _decode_json_pointer(pointer: str) -> tuple[str, ...] | None:
        if not pointer.startswith("/"):
            return None
        tokens: list[str] = []
        for raw_token in pointer[1:].split("/"):
            index = 0
            decoded = ""
            while index < len(raw_token):
                if raw_token[index] != "~":
                    decoded += raw_token[index]
                    index += 1
                    continue
                if index + 1 >= len(raw_token) or raw_token[index + 1] not in {"0", "1"}:
                    return None
                decoded += "~" if raw_token[index + 1] == "0" else "/"
                index += 2
            tokens.append(decoded)
        return tuple(tokens)

    @classmethod
    def _sensitive_schema_paths(
        cls,
        schema: JsonObject,
        prefix: tuple[str, ...] = (),
    ) -> set[tuple[str, ...]]:
        found: set[tuple[str, ...]] = set()
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return found
        for name, property_schema in properties.items():
            if not isinstance(name, str) or not isinstance(property_schema, Mapping):
                continue
            path = (*prefix, name)
            if property_schema.get("x-sensitive") is True:
                found.add(path)
            found.update(cls._sensitive_schema_paths(property_schema, path))
        return found

    @staticmethod
    def _parse_effect(
        raw: str,
        diagnostics: list[ManifestDiagnostic],
    ) -> ToolEffect | None:
        try:
            return ToolEffect(raw.strip().lower())
        except ValueError:
            diagnostics.append(
                ToolManifestValidator._diagnostic(
                    "manifest.effect.unsafe_capability",
                    ("effect",),
                    "effect must be one supported explicit classification",
                )
            )
            return None

    @staticmethod
    def _parse_idempotency(
        raw: str,
        diagnostics: list[ManifestDiagnostic],
    ) -> IdempotencyMode | None:
        try:
            return IdempotencyMode(raw.strip().lower())
        except ValueError:
            diagnostics.append(
                ToolManifestValidator._diagnostic(
                    "manifest.effect.unsafe_capability",
                    ("idempotency",),
                    "idempotency mode is not supported",
                )
            )
            return None

    @staticmethod
    def _parse_reconciliation(
        raw: str,
        diagnostics: list[ManifestDiagnostic],
    ) -> ReconciliationMode | None:
        try:
            return ReconciliationMode(raw.strip().lower())
        except ValueError:
            diagnostics.append(
                ToolManifestValidator._diagnostic(
                    "manifest.effect.unsafe_capability",
                    ("reconciliation",),
                    "reconciliation mode is not supported",
                )
            )
            return None

    @staticmethod
    def _validate_effect_matrix(
        *,
        effect: ToolEffect | None,
        idempotency: IdempotencyMode | None,
        reconciliation: ReconciliationMode | None,
        diagnostics: list[ManifestDiagnostic],
    ) -> None:
        if effect is None or idempotency is None or reconciliation is None:
            return
        if effect is ToolEffect.READ_ONLY:
            valid = reconciliation is ReconciliationMode.NONE
        else:
            valid = (
                idempotency is IdempotencyMode.REQUIRED
                and reconciliation is ReconciliationMode.BY_IDEMPOTENCY_KEY
            )
        if not valid:
            diagnostics.append(
                ToolManifestValidator._diagnostic(
                    "manifest.effect.unsafe_capability",
                    ("effect",),
                    "effect requires a safe idempotency and reconciliation declaration",
                )
            )

    @staticmethod
    def _diagnostic(
        code: str,
        path: DiagnosticPath,
        message: str,
    ) -> ManifestDiagnostic:
        return ManifestDiagnostic(code=code, path=path, message=message)

    @staticmethod
    def _ordered_diagnostics(
        diagnostics: list[ManifestDiagnostic],
    ) -> tuple[ManifestDiagnostic, ...]:
        unique = set(diagnostics)
        ordered = sorted(
            unique,
            key=lambda item: (
                tuple((isinstance(segment, int), str(segment)) for segment in item.path),
                item.code,
                item.message,
            ),
        )
        if len(ordered) <= _MAX_DIAGNOSTICS:
            return tuple(ordered)
        return (
            *ordered[: _MAX_DIAGNOSTICS - 1],
            ManifestDiagnostic(
                code="manifest.bounds.exceeded",
                path=(),
                message="additional validation diagnostics were safely truncated",
            ),
        )
