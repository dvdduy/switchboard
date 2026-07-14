import asyncio
from datetime import UTC, datetime
from operator import setitem
from typing import TypeVar
from uuid import uuid4

import pytest

from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.application.ports.tool_adapter import (
    ToolInvocationFailure,
    ToolInvocationRequest,
    ToolInvocationSuccess,
    ToolReconciliationResult,
)
from switchboard.application.services.tool_conformance import (
    CONFORMANCE_IDEMPOTENCY_KEY,
    REDACTED_VALUE,
    ToolConformanceRunner,
    ToolConformanceSuite,
    redact_json,
)
from switchboard.domain.identifiers import (
    ToolConformanceCaseResultId,
    ToolConformanceRunId,
    ToolDefinitionId,
    ToolVersionId,
)
from switchboard.domain.tools import (
    JSON_SCHEMA_DRAFT_2020_12,
    TOOL_MANIFEST_SCHEMA_VERSION,
    IdempotencyMode,
    ReconciliationMode,
    RetryPolicy,
    ToolConformanceStatus,
    ToolEffect,
    ToolManifest,
    ToolVersion,
)

NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)
IdentifierT = TypeVar("IdentifierT")


class FixedClock:
    def now(self) -> datetime:
        return NOW


class UuidGenerator[IdentifierT]:
    def __init__(self, constructor: type[IdentifierT]) -> None:
        self._constructor = constructor

    def new(self) -> IdentifierT:
        return self._constructor(uuid4())  # type: ignore[call-arg]


class FakeToolRepository:
    def __init__(self, factory: "FakeUnitOfWorkFactory") -> None:
        self._factory = factory
        self.saved: list[tuple[object, tuple[object, ...]]] = []

    async def add_conformance_run(self, run: object, cases: tuple[object, ...]) -> None:
        assert self._factory.transaction_open
        self.saved.append((run, cases))


class FakeUnitOfWork:
    def __init__(self, factory: "FakeUnitOfWorkFactory") -> None:
        self._factory = factory
        self.tools = factory.tools
        self.committed = False

    async def __aenter__(self) -> "FakeUnitOfWork":
        assert not self._factory.transaction_open
        self._factory.transaction_open = True
        self._factory.opened += 1
        return self

    async def __aexit__(self, *args: object) -> None:
        self._factory.transaction_open = False

    async def commit(self) -> None:
        self.committed = True


class FakeUnitOfWorkFactory:
    def __init__(self) -> None:
        self.transaction_open = False
        self.opened = 0
        self.tools = FakeToolRepository(self)

    def __call__(self) -> FakeUnitOfWork:
        return FakeUnitOfWork(self)


class FakeResolver:
    def __init__(self, adapter: object | None) -> None:
        self.adapter = adapter

    def resolve(self, adapter_key: str) -> object | None:
        assert adapter_key == "reference.conformance.v1"
        return self.adapter


class RecordingAdapter:
    def __init__(self, factory: FakeUnitOfWorkFactory) -> None:
        self.factory = factory
        self.requests: list[ToolInvocationRequest] = []
        self.reconciled_keys: list[str] = []
        self.invalid_output = False
        self.raise_secret = False
        self.slow_timeout = False

    async def invoke(
        self,
        request: ToolInvocationRequest,
    ) -> ToolInvocationSuccess | ToolInvocationFailure:
        assert not self.factory.transaction_open
        self.requests.append(request)
        scenario = request.arguments["scenario"]
        if scenario == "timeout" and self.slow_timeout:
            await asyncio.sleep(1)
        if self.raise_secret:
            raise RuntimeError("provider leaked secret-value")
        if scenario == "error":
            return ToolInvocationFailure("temporarily_unavailable", retryable=True)
        if self.invalid_output and scenario == "valid":
            return ToolInvocationSuccess({"wrong": True})
        if scenario in {"idempotency", "reconcile"} and (
            request.idempotency_key != CONFORMANCE_IDEMPOTENCY_KEY
        ):
            return ToolInvocationFailure("missing_idempotency", retryable=False)
        return ToolInvocationSuccess({"result": "ok"})

    async def reconcile(self, idempotency_key: str) -> ToolReconciliationResult:
        assert not self.factory.transaction_open
        self.reconciled_keys.append(idempotency_key)
        return ToolReconciliationResult(found=True, output={"result": "ok"})


def object_schema(*, sensitive: bool = False) -> dict[str, object]:
    properties: dict[str, object] = {"scenario": {"type": "string"}}
    if sensitive:
        properties["secret"] = {"type": "string", "x-sensitive": True}
    return {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": ["scenario"],
    }


def output_schema() -> dict[str, object]:
    return {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "result": {"type": "string"},
            "secret": {"type": "string", "x-sensitive": True},
        },
        "required": ["result"],
    }


def version(*, timeout_ms: int = 100) -> ToolVersion:
    manifest = ToolManifest(
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
        display_name="Conformance tool",
        description="Exercises the normalized adapter contract.",
        input_schema=object_schema(sensitive=True),
        output_schema=output_schema(),
        effect=ToolEffect.MUTATING,
        required_scopes=("tools:write",),
        timeout_ms=timeout_ms,
        retry_policy=RetryPolicy(2, 1, ("temporarily_unavailable",)),
        idempotency=IdempotencyMode.REQUIRED,
        reconciliation=ReconciliationMode.BY_IDEMPOTENCY_KEY,
        adapter_key="reference.conformance.v1",
        sensitive_input_paths=("/secret",),
        sensitive_output_paths=("/secret",),
    )
    return ToolVersion(
        id=ToolVersionId(uuid4()),
        tool_definition_id=ToolDefinitionId(uuid4()),
        version_number=1,
        manifest=manifest,
        content_hash=manifest.content_hash,
        created_at=NOW,
    )


def suite() -> ToolConformanceSuite:
    return ToolConformanceSuite(
        valid_input={"scenario": "valid"},
        invalid_input={},
        invalid_output={"wrong": True},
        timeout_input={"scenario": "timeout"},
        declared_error_input={"scenario": "error"},
        expected_error_code="temporarily_unavailable",
        idempotency_input={"scenario": "idempotency"},
        reconciliation_input={"scenario": "reconcile"},
        sensitive_input={"scenario": "valid", "secret": "input-secret"},
        sensitive_output={"result": "ok", "secret": "output-secret"},
    )


def runner(
    factory: FakeUnitOfWorkFactory,
    adapter: object | None,
) -> ToolConformanceRunner:
    return ToolConformanceRunner(
        adapter_resolver=FakeResolver(adapter),  # type: ignore[arg-type]
        schema_validator=Draft202012JsonSchemaValidator(),
        unit_of_work_factory=factory,  # type: ignore[arg-type]
        clock=FixedClock(),
        run_id_generator=UuidGenerator[ToolConformanceRunId](
            ToolConformanceRunId  # type: ignore[arg-type]
        ),
        case_id_generator=UuidGenerator[ToolConformanceCaseResultId](
            ToolConformanceCaseResultId  # type: ignore[arg-type]
        ),
    )


def result_map(report: object) -> dict[str, object]:
    return {result.case_key: result for result in report.case_results}  # type: ignore[attr-defined]


async def test_complete_suite_runs_without_transaction_and_persists_once() -> None:
    factory = FakeUnitOfWorkFactory()
    adapter = RecordingAdapter(factory)

    report = await runner(factory, adapter).run(version=version(), suite=suite())

    assert report.run.status is ToolConformanceStatus.PASSED
    assert len(report.case_results) == 8
    assert all(result.diagnostic_code is None for result in report.case_results)
    assert factory.opened == 1
    assert factory.tools.saved == [(report.run, report.case_results)]
    assert {request.arguments["scenario"] for request in adapter.requests} == {
        "valid",
        "timeout",
        "error",
        "idempotency",
        "reconcile",
    }
    assert adapter.reconciled_keys == [CONFORMANCE_IDEMPOTENCY_KEY]


async def test_invalid_adapter_output_fails_with_safe_diagnostic() -> None:
    factory = FakeUnitOfWorkFactory()
    adapter = RecordingAdapter(factory)
    adapter.invalid_output = True

    report = await runner(factory, adapter).run(version=version(), suite=suite())

    valid_io = result_map(report)["valid_input_output"]
    assert report.run.status is ToolConformanceStatus.FAILED
    assert valid_io.diagnostic_code == "conformance.invalid_output"  # type: ignore[attr-defined]


async def test_timeout_is_bounded_and_persisted_as_failure() -> None:
    factory = FakeUnitOfWorkFactory()
    adapter = RecordingAdapter(factory)
    adapter.slow_timeout = True

    report = await runner(factory, adapter).run(version=version(timeout_ms=1), suite=suite())

    timeout = result_map(report)["timeout"]
    assert timeout.diagnostic_code == "conformance.timeout"  # type: ignore[attr-defined]
    assert report.run.status is ToolConformanceStatus.FAILED


async def test_adapter_exception_never_leaks_provider_message() -> None:
    factory = FakeUnitOfWorkFactory()
    adapter = RecordingAdapter(factory)
    adapter.raise_secret = True

    report = await runner(factory, adapter).run(version=version(), suite=suite())

    rendered = " ".join(result.diagnostic_code or "" for result in report.case_results)
    assert "secret-value" not in rendered
    assert "conformance.adapter_exception" in rendered


async def test_cancellation_before_persistence_leaves_no_partial_run() -> None:
    factory = FakeUnitOfWorkFactory()
    adapter = RecordingAdapter(factory)
    adapter.slow_timeout = True
    task = asyncio.create_task(
        runner(factory, adapter).run(version=version(timeout_ms=30_000), suite=suite())
    )

    while not adapter.requests:
        await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert factory.opened == 0
    assert factory.tools.saved == []


def test_redaction_returns_an_immutable_copy_without_sensitive_values() -> None:
    source = {
        "nested": {"token": "input-secret"},
        "items": [{"token": "output-secret"}],
    }

    redacted, complete = redact_json(source, ("/nested/token", "/items/0/token"))

    assert complete
    assert redacted == {
        "nested": {"token": REDACTED_VALUE},
        "items": ({"token": REDACTED_VALUE},),
    }
    assert source["nested"] == {"token": "input-secret"}
    with pytest.raises(TypeError):
        setitem(redacted, "other", True)
