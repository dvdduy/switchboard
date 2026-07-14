from datetime import UTC, datetime, timedelta
from operator import setitem
from typing import cast
from uuid import uuid4

import pytest

from switchboard.domain.errors import DomainValidationError, InvalidStateTransition
from switchboard.domain.identifiers import (
    TeamId,
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
    ToolConformanceCaseResult,
    ToolConformanceStatus,
    ToolDefinition,
    ToolEffect,
    ToolLifecycleStatus,
    ToolManifest,
    ToolVersion,
    ToolVersionState,
)

NOW = datetime(2026, 7, 13, 16, 0, tzinfo=UTC)


def object_schema(*, reversed_order: bool = False) -> dict[str, object]:
    items = [
        ("$schema", JSON_SCHEMA_DRAFT_2020_12),
        ("type", "object"),
        ("additionalProperties", False),
        (
            "properties",
            {"query": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}},
        ),
    ]
    return dict(reversed(items) if reversed_order else items)


def manifest(
    *,
    reversed_schemas: bool = False,
    input_schema: dict[str, object] | None = None,
) -> ToolManifest:
    return ToolManifest(
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
        display_name=" Search work items ",
        description=" Search the team's work items. ",
        input_schema=(
            object_schema(reversed_order=reversed_schemas) if input_schema is None else input_schema
        ),
        output_schema=object_schema(reversed_order=reversed_schemas),
        effect=ToolEffect.READ_ONLY,
        required_scopes=("work_items:read",),
        timeout_ms=2_000,
        retry_policy=RetryPolicy(2, 100, ("temporarily_unavailable",)),
        idempotency=IdempotencyMode.NONE,
        reconciliation=ReconciliationMode.NONE,
        adapter_key=" reference.search_work_items.v1 ",
    )


def test_manifest_is_recursively_immutable_and_defensively_copied() -> None:
    input_schema = object_schema()
    tool_manifest = manifest(input_schema=input_schema)

    cast(dict[str, object], input_schema["properties"])["later"] = {"type": "boolean"}

    with pytest.raises(TypeError):
        setitem(tool_manifest.input_schema, "type", "array")

    properties = cast(dict[str, object], tool_manifest.input_schema["properties"])
    with pytest.raises(TypeError):
        setitem(properties, "later", {"type": "boolean"})

    assert tool_manifest.display_name == "Search work items"
    assert tool_manifest.adapter_key == "reference.search_work_items.v1"
    assert "later" not in properties


def test_manifest_hash_is_stable_across_mapping_insertion_order() -> None:
    assert manifest().content_hash == manifest(reversed_schemas=True).content_hash


def test_definition_normalizes_team_owned_identity() -> None:
    definition = ToolDefinition(
        id=ToolDefinitionId(uuid4()),
        team_id=TeamId(uuid4()),
        tool_key=" Search_Work-Items.V1 ",
        created_at=NOW,
    )

    assert definition.tool_key == "search_work-items.v1"


def test_version_requires_hash_of_immutable_manifest_content() -> None:
    tool_manifest = manifest()

    with pytest.raises(DomainValidationError, match="content_hash"):
        ToolVersion(
            id=ToolVersionId(uuid4()),
            tool_definition_id=ToolDefinitionId(uuid4()),
            version_number=1,
            manifest=tool_manifest,
            content_hash="0" * 64,
            created_at=NOW,
        )


@pytest.mark.parametrize(
    ("effect", "idempotency", "reconciliation"),
    [
        (ToolEffect.READ_ONLY, IdempotencyMode.NONE, ReconciliationMode.BY_IDEMPOTENCY_KEY),
        (ToolEffect.MUTATING, IdempotencyMode.NONE, ReconciliationMode.BY_IDEMPOTENCY_KEY),
        (ToolEffect.PRIVILEGED, IdempotencyMode.REQUIRED, ReconciliationMode.NONE),
    ],
)
def test_manifest_rejects_unsafe_effect_combinations(
    effect: ToolEffect,
    idempotency: IdempotencyMode,
    reconciliation: ReconciliationMode,
) -> None:
    with pytest.raises(DomainValidationError):
        ToolManifest(
            schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
            display_name="Unsafe",
            description="An unsafe declaration.",
            input_schema=object_schema(),
            output_schema=object_schema(),
            effect=effect,
            required_scopes=("tools:invoke",),
            timeout_ms=100,
            retry_policy=RetryPolicy(1, 0, ()),
            idempotency=idempotency,
            reconciliation=reconciliation,
            adapter_key="unsafe.v1",
        )


def test_tool_version_lifecycle_preserves_activation_proof() -> None:
    run_id = ToolConformanceRunId(uuid4())
    state = ToolVersionState(
        tool_version_id=ToolVersionId(uuid4()),
        status=ToolLifecycleStatus.DRAFT,
        revision=1,
        activated_conformance_run_id=None,
        created_at=NOW,
        updated_at=NOW,
    )

    active = state.activate(conformance_run_id=run_id, at=NOW + timedelta(seconds=1))
    deprecated = active.deprecate(at=NOW + timedelta(seconds=2))
    disabled = deprecated.disable(at=NOW + timedelta(seconds=3))

    assert (active.status, deprecated.status, disabled.status) == (
        ToolLifecycleStatus.ACTIVE,
        ToolLifecycleStatus.DEPRECATED,
        ToolLifecycleStatus.DISABLED,
    )
    assert disabled.revision == 4
    assert disabled.activated_conformance_run_id == run_id

    with pytest.raises(InvalidStateTransition, match="terminal"):
        disabled.disable(at=NOW + timedelta(seconds=4))


def test_active_state_requires_conformance_proof() -> None:
    with pytest.raises(DomainValidationError, match="activation run"):
        ToolVersionState(
            tool_version_id=ToolVersionId(uuid4()),
            status=ToolLifecycleStatus.ACTIVE,
            revision=1,
            activated_conformance_run_id=None,
            created_at=NOW,
            updated_at=NOW,
        )


def test_conformance_case_diagnostics_match_failure_status() -> None:
    run_id = ToolConformanceRunId(uuid4())

    with pytest.raises(DomainValidationError, match="must not have a diagnostic"):
        ToolConformanceCaseResult(
            id=ToolConformanceCaseResultId(uuid4()),
            run_id=run_id,
            case_key="valid_input",
            status=ToolConformanceStatus.PASSED,
            duration_ms=1,
            diagnostic_code="schema.invalid",
        )

    with pytest.raises(DomainValidationError, match="requires a diagnostic"):
        ToolConformanceCaseResult(
            id=ToolConformanceCaseResultId(uuid4()),
            run_id=run_id,
            case_key="valid_input",
            status=ToolConformanceStatus.FAILED,
            duration_ms=1,
            diagnostic_code=None,
        )
