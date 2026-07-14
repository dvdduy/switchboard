"""Deterministic conformance execution for validated tool versions."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from switchboard.application.ports.clock import Clock
from switchboard.application.ports.id_generator import IdGenerator
from switchboard.application.ports.json_schema import JsonSchemaValidator
from switchboard.application.ports.tool_adapter import (
    ToolAdapter,
    ToolAdapterResolver,
    ToolInvocationFailure,
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolInvocationSuccess,
)
from switchboard.application.ports.unit_of_work import UnitOfWorkFactory
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    ToolConformanceCaseResultId,
    ToolConformanceRunId,
)
from switchboard.domain.json_values import (
    JsonObject,
    freeze_json_object,
    mutable_json_value,
)
from switchboard.domain.tools import (
    IdempotencyMode,
    ReconciliationMode,
    ToolConformanceCaseResult,
    ToolConformanceRun,
    ToolConformanceStatus,
    ToolManifest,
    ToolVersion,
)

REDACTED_VALUE = "[REDACTED]"
CONFORMANCE_IDEMPOTENCY_KEY = "switchboard-conformance"


@dataclass(frozen=True, slots=True)
class ToolConformanceSuite:
    """Platform-owned synthetic values for one manifest contract."""

    valid_input: JsonObject
    invalid_input: JsonObject
    invalid_output: JsonObject
    timeout_input: JsonObject
    declared_error_input: JsonObject
    expected_error_code: str
    idempotency_input: JsonObject
    reconciliation_input: JsonObject
    sensitive_input: JsonObject
    sensitive_output: JsonObject

    def __post_init__(self) -> None:
        for field_name in (
            "valid_input",
            "invalid_input",
            "invalid_output",
            "timeout_input",
            "declared_error_input",
            "idempotency_input",
            "reconciliation_input",
            "sensitive_input",
            "sensitive_output",
        ):
            object.__setattr__(
                self,
                field_name,
                freeze_json_object(getattr(self, field_name), field_name=field_name),
            )
        expected_error_code = self.expected_error_code.strip().lower()
        if not expected_error_code or len(expected_error_code) > 100:
            raise DomainValidationError(
                "expected_error_code must contain between 1 and 100 characters"
            )
        object.__setattr__(self, "expected_error_code", expected_error_code)


@dataclass(frozen=True, slots=True)
class ToolConformanceReport:
    """Complete immutable run plus its ordered safe case results."""

    run: ToolConformanceRun
    case_results: tuple[ToolConformanceCaseResult, ...]


CaseCheck = Callable[[], Awaitable[str | None]]


class ToolConformanceRunner:
    """Run synthetic probes outside transactions and persist one complete report."""

    def __init__(
        self,
        *,
        adapter_resolver: ToolAdapterResolver,
        schema_validator: JsonSchemaValidator,
        unit_of_work_factory: UnitOfWorkFactory,
        clock: Clock,
        run_id_generator: IdGenerator[ToolConformanceRunId],
        case_id_generator: IdGenerator[ToolConformanceCaseResultId],
    ) -> None:
        self._adapter_resolver = adapter_resolver
        self._schema_validator = schema_validator
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._run_id_generator = run_id_generator
        self._case_id_generator = case_id_generator

    async def run(
        self,
        *,
        version: ToolVersion,
        suite: ToolConformanceSuite,
    ) -> ToolConformanceReport:
        started_at = self._clock.now()
        adapter = self._adapter_resolver.resolve(version.manifest.adapter_key)
        checks: tuple[tuple[str, CaseCheck], ...] = (
            (
                "valid_input_output",
                lambda: self._check_valid_io(adapter, version.manifest, suite.valid_input),
            ),
            (
                "invalid_input_rejected",
                lambda: self._check_invalid_value(
                    suite.invalid_input,
                    version.manifest.input_schema,
                    "conformance.invalid_input_accepted",
                ),
            ),
            (
                "invalid_output_rejected",
                lambda: self._check_invalid_value(
                    suite.invalid_output,
                    version.manifest.output_schema,
                    "conformance.invalid_output_accepted",
                ),
            ),
            (
                "timeout",
                lambda: self._check_timeout(adapter, version.manifest, suite.timeout_input),
            ),
            (
                "declared_error_mapping",
                lambda: self._check_declared_error(
                    adapter,
                    version.manifest,
                    suite.declared_error_input,
                    suite.expected_error_code,
                ),
            ),
            (
                "idempotency_key_propagation",
                lambda: self._check_idempotency(
                    adapter,
                    version.manifest,
                    suite.idempotency_input,
                ),
            ),
            (
                "reconciliation",
                lambda: self._check_reconciliation(
                    adapter,
                    version.manifest,
                    suite.reconciliation_input,
                ),
            ),
            (
                "sensitive_field_redaction",
                lambda: self._check_redaction(version.manifest, suite),
            ),
        )

        run_id = self._run_id_generator.new()
        case_results: list[ToolConformanceCaseResult] = []
        for case_key, check in checks:
            diagnostic_code = await check()
            case_results.append(
                ToolConformanceCaseResult(
                    id=self._case_id_generator.new(),
                    run_id=run_id,
                    case_key=case_key,
                    status=(
                        ToolConformanceStatus.PASSED
                        if diagnostic_code is None
                        else ToolConformanceStatus.FAILED
                    ),
                    duration_ms=0,
                    diagnostic_code=diagnostic_code,
                )
            )
        case_results.sort(key=lambda result: result.case_key)

        completed_at = self._clock.now()
        status = (
            ToolConformanceStatus.PASSED
            if all(result.status is ToolConformanceStatus.PASSED for result in case_results)
            else ToolConformanceStatus.FAILED
        )
        run = ToolConformanceRun(
            id=run_id,
            tool_version_id=version.id,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
        )
        report = ToolConformanceReport(run=run, case_results=tuple(case_results))

        async with self._unit_of_work_factory() as unit_of_work:
            await unit_of_work.tools.add_conformance_run(run, report.case_results)
            await unit_of_work.commit()

        return report

    async def _check_valid_io(
        self,
        adapter: ToolAdapter | None,
        manifest: ToolManifest,
        arguments: JsonObject,
    ) -> str | None:
        if self._schema_validator.validate_instance(
            instance=arguments,
            schema=manifest.input_schema,
        ):
            return "conformance.synthetic_input_invalid"
        result = await self._invoke(
            adapter,
            manifest,
            arguments,
            idempotency_key=self._case_idempotency_key(manifest, "valid"),
        )
        if isinstance(result, str):
            return result
        if isinstance(result, ToolInvocationFailure):
            return "conformance.unexpected_declared_error"
        if self._schema_validator.validate_instance(
            instance=result.output,
            schema=manifest.output_schema,
        ):
            return "conformance.invalid_output"
        return None

    async def _check_invalid_value(
        self,
        value: JsonObject,
        schema: JsonObject,
        accepted_code: str,
    ) -> str | None:
        return (
            None
            if self._schema_validator.validate_instance(instance=value, schema=schema)
            else accepted_code
        )

    async def _check_timeout(
        self,
        adapter: ToolAdapter | None,
        manifest: ToolManifest,
        arguments: JsonObject,
    ) -> str | None:
        result = await self._invoke(
            adapter,
            manifest,
            arguments,
            idempotency_key=self._case_idempotency_key(manifest, "timeout"),
        )
        if isinstance(result, str):
            return result
        if isinstance(result, ToolInvocationFailure):
            return "conformance.unexpected_declared_error"
        return (
            "conformance.invalid_output"
            if self._schema_validator.validate_instance(
                instance=result.output,
                schema=manifest.output_schema,
            )
            else None
        )

    async def _check_declared_error(
        self,
        adapter: ToolAdapter | None,
        manifest: ToolManifest,
        arguments: JsonObject,
        expected_error_code: str,
    ) -> str | None:
        result = await self._invoke(
            adapter,
            manifest,
            arguments,
            idempotency_key=self._case_idempotency_key(manifest, "error"),
        )
        if isinstance(result, str):
            return result
        if not isinstance(result, ToolInvocationFailure):
            return "conformance.declared_error_not_returned"
        retryable = expected_error_code in manifest.retry_policy.retryable_error_codes
        if result.error_code != expected_error_code or result.retryable is not retryable:
            return "conformance.declared_error_mismatch"
        return None

    async def _check_idempotency(
        self,
        adapter: ToolAdapter | None,
        manifest: ToolManifest,
        arguments: JsonObject,
    ) -> str | None:
        if manifest.idempotency is IdempotencyMode.NONE:
            return None
        result = await self._invoke(
            adapter,
            manifest,
            arguments,
            idempotency_key=CONFORMANCE_IDEMPOTENCY_KEY,
        )
        if isinstance(result, str):
            return result
        if not isinstance(result, ToolInvocationSuccess):
            return "conformance.idempotency_not_proven"
        return (
            "conformance.invalid_output"
            if self._schema_validator.validate_instance(
                instance=result.output,
                schema=manifest.output_schema,
            )
            else None
        )

    async def _check_reconciliation(
        self,
        adapter: ToolAdapter | None,
        manifest: ToolManifest,
        arguments: JsonObject,
    ) -> str | None:
        if manifest.reconciliation is ReconciliationMode.NONE:
            return None
        if adapter is None:
            return "conformance.adapter_not_found"
        invocation = await self._invoke(
            adapter,
            manifest,
            arguments,
            idempotency_key=CONFORMANCE_IDEMPOTENCY_KEY,
        )
        if isinstance(invocation, str):
            return invocation
        if not isinstance(invocation, ToolInvocationSuccess):
            return "conformance.reconciliation_not_proven"
        try:
            result = await asyncio.wait_for(
                adapter.reconcile(CONFORMANCE_IDEMPOTENCY_KEY),
                timeout=manifest.timeout_ms / 1_000,
            )
        except TimeoutError:
            return "conformance.timeout"
        except Exception:
            return "conformance.adapter_exception"
        if not result.found or result.output is None:
            return "conformance.reconciliation_not_proven"
        return (
            "conformance.invalid_output"
            if self._schema_validator.validate_instance(
                instance=result.output,
                schema=manifest.output_schema,
            )
            else None
        )

    async def _check_redaction(
        self,
        manifest: ToolManifest,
        suite: ToolConformanceSuite,
    ) -> str | None:
        _, input_complete = redact_json(suite.sensitive_input, manifest.sensitive_input_paths)
        _, output_complete = redact_json(
            suite.sensitive_output,
            manifest.sensitive_output_paths,
        )
        return None if input_complete and output_complete else "conformance.redaction_failed"

    async def _invoke(
        self,
        adapter: ToolAdapter | None,
        manifest: ToolManifest,
        arguments: JsonObject,
        *,
        idempotency_key: str | None,
    ) -> ToolInvocationResult | str:
        if self._schema_validator.validate_instance(
            instance=arguments,
            schema=manifest.input_schema,
        ):
            return "conformance.synthetic_input_invalid"
        if adapter is None:
            return "conformance.adapter_not_found"
        try:
            return await asyncio.wait_for(
                adapter.invoke(
                    ToolInvocationRequest(
                        arguments=arguments,
                        idempotency_key=idempotency_key,
                    )
                ),
                timeout=manifest.timeout_ms / 1_000,
            )
        except TimeoutError:
            return "conformance.timeout"
        except Exception:
            return "conformance.adapter_exception"

    @staticmethod
    def _case_idempotency_key(manifest: ToolManifest, case_key: str) -> str | None:
        if manifest.idempotency is IdempotencyMode.NONE:
            return None
        return f"{CONFORMANCE_IDEMPOTENCY_KEY}-{case_key}"


def redact_json(value: JsonObject, pointers: Sequence[str]) -> tuple[JsonObject, bool]:
    """Return a redacted copy and whether every declared pointer was present."""

    mutable = mutable_json_value(value)
    if not isinstance(mutable, dict):
        raise TypeError("JSON object did not thaw to a dictionary")
    complete = True
    for pointer in pointers:
        tokens = _decode_json_pointer(pointer)
        if not tokens or not _replace_pointer(mutable, tokens):
            complete = False
    return freeze_json_object(mutable, field_name="redacted"), complete


def _replace_pointer(root: object, tokens: tuple[str, ...]) -> bool:
    current = root
    for token in tokens[:-1]:
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdecimal() and int(token) < len(current):
            current = current[int(token)]
        else:
            return False
    final = tokens[-1]
    if isinstance(current, dict) and final in current:
        current[final] = REDACTED_VALUE
        return True
    if isinstance(current, list) and final.isdecimal() and int(final) < len(current):
        current[int(final)] = REDACTED_VALUE
        return True
    return False


def _decode_json_pointer(pointer: str) -> tuple[str, ...] | None:
    if not pointer.startswith("/") or pointer == "/":
        return None
    tokens: list[str] = []
    for raw in pointer[1:].split("/"):
        decoded = ""
        index = 0
        while index < len(raw):
            if raw[index] != "~":
                decoded += raw[index]
                index += 1
            elif index + 1 < len(raw) and raw[index + 1] in {"0", "1"}:
                decoded += "~" if raw[index + 1] == "0" else "/"
                index += 2
            else:
                return None
        tokens.append(decoded)
    return tuple(tokens)
