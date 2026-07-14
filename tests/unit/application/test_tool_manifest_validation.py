from dataclasses import replace

import pytest

from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.application.services.tool_manifest_validation import ToolManifestValidator
from switchboard.domain.tools import (
    JSON_SCHEMA_DRAFT_2020_12,
    TOOL_MANIFEST_SCHEMA_VERSION,
    ManifestValidationResult,
    RetryPolicyCandidate,
    ToolManifestCandidate,
)


def schema(*, sensitive: bool = False) -> dict[str, object]:
    property_schema: dict[str, object] = {"type": "string"}
    if sensitive:
        property_schema["x-sensitive"] = True
    return {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": {"value": property_schema},
        "required": ["value"],
    }


def candidate() -> ToolManifestCandidate:
    return ToolManifestCandidate(
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
        display_name="Read value",
        description="Reads a value without changing external state.",
        input_schema=schema(),
        output_schema=schema(),
        effect="read_only",
        required_scopes=("values:read",),
        timeout_ms=2_000,
        retry_policy=RetryPolicyCandidate(2, 100, ("temporarily_unavailable",)),
        idempotency="none",
        reconciliation="none",
        adapter_key="reference.read_value.v1",
    )


@pytest.fixture
def validator() -> ToolManifestValidator:
    return ToolManifestValidator(Draft202012JsonSchemaValidator())


def diagnostic_codes(result: ManifestValidationResult) -> set[str]:
    return {item.code for item in result.diagnostics}


def test_valid_candidate_returns_normalized_immutable_manifest(
    validator: ToolManifestValidator,
) -> None:
    result = validator.validate(
        replace(candidate(), required_scopes=("Values:Read", "values:read"))
    )

    assert result.is_valid
    assert result.diagnostics == ()
    assert result.manifest is not None
    assert result.manifest.required_scopes == ("values:read",)
    assert len(result.manifest.content_hash) == 64


def test_mutating_candidate_requires_idempotency_and_reconciliation(
    validator: ToolManifestValidator,
) -> None:
    result = validator.validate(replace(candidate(), effect="mutating"))

    assert not result.is_valid
    assert "manifest.effect.unsafe_capability" in diagnostic_codes(result)


def test_schema_references_and_unsupported_draft_are_rejected(
    validator: ToolManifestValidator,
) -> None:
    unsafe_schema = schema()
    unsafe_schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    unsafe_schema["properties"] = {"value": {"$ref": "https://attacker.invalid/schema"}}

    result = validator.validate(replace(candidate(), input_schema=unsafe_schema))

    assert {
        "manifest.schema.reference_forbidden",
        "manifest.schema.unsupported_draft",
    } <= diagnostic_codes(result)
    assert all("attacker.invalid" not in item.message for item in result.diagnostics)


def test_schema_library_diagnostic_does_not_echo_rejected_content(
    validator: ToolManifestValidator,
) -> None:
    invalid_schema = schema()
    invalid_schema["required"] = "super-secret-value"

    result = validator.validate(replace(candidate(), input_schema=invalid_schema))

    assert "manifest.schema.invalid" in diagnostic_codes(result)
    assert all("super-secret-value" not in item.message for item in result.diagnostics)


def test_overdeep_schema_is_bounded_before_library_validation(
    validator: ToolManifestValidator,
) -> None:
    nested: dict[str, object] = {"type": "string"}
    for _ in range(25):
        nested = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"child": nested},
        }
    deep_schema = {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": {"value": nested},
    }

    result = validator.validate(replace(candidate(), input_schema=deep_schema))

    assert "manifest.bounds.exceeded" in diagnostic_codes(result)


def test_sensitive_schema_properties_require_matching_json_pointers(
    validator: ToolManifestValidator,
) -> None:
    missing = validator.validate(replace(candidate(), input_schema=schema(sensitive=True)))
    wrong = validator.validate(
        replace(
            candidate(),
            input_schema=schema(sensitive=True),
            sensitive_input_paths=("/missing",),
        )
    )
    valid = validator.validate(
        replace(
            candidate(),
            input_schema=schema(sensitive=True),
            sensitive_input_paths=("/value",),
        )
    )

    assert "manifest.redaction.invalid_path" in diagnostic_codes(missing)
    assert "manifest.redaction.invalid_path" in diagnostic_codes(wrong)
    assert valid.is_valid


def test_diagnostics_are_deterministic_and_do_not_echo_untrusted_values(
    validator: ToolManifestValidator,
) -> None:
    invalid = replace(
        candidate(),
        adapter_key="https://user:secret@example.invalid/tool",
        effect="secret-effect-name",
        timeout_ms=0,
        required_scopes=("INVALID SECRET",),
    )

    first = validator.validate(invalid)
    second = validator.validate(invalid)

    assert first.diagnostics == second.diagnostics
    rendered = " ".join(item.message for item in first.diagnostics)
    assert "secret" not in rendered.lower()


@pytest.mark.parametrize("timeout_ms", [0, 30_001])
def test_timeout_bounds_are_enforced(
    validator: ToolManifestValidator,
    timeout_ms: int,
) -> None:
    result = validator.validate(replace(candidate(), timeout_ms=timeout_ms))

    assert "manifest.bounds.exceeded" in diagnostic_codes(result)
