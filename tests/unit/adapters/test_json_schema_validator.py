from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.domain.json_values import freeze_json_object
from switchboard.domain.tools import JSON_SCHEMA_DRAFT_2020_12


def object_schema() -> dict[str, object]:
    return {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }


def test_validator_accepts_valid_draft_2020_12_schema() -> None:
    validator = Draft202012JsonSchemaValidator()

    assert validator.validate_schema(freeze_json_object(object_schema(), field_name="schema")) == ()


def test_validator_reports_safe_schema_diagnostics() -> None:
    validator = Draft202012JsonSchemaValidator()
    invalid = object_schema()
    invalid["required"] = "private rejected value"

    issues = validator.validate_schema(freeze_json_object(invalid, field_name="schema"))

    assert issues
    assert all(issue.message == "schema does not satisfy Draft 2020-12" for issue in issues)
    assert all("private rejected value" not in issue.message for issue in issues)


def test_validator_checks_instances_without_echoing_values() -> None:
    validator = Draft202012JsonSchemaValidator()
    schema = freeze_json_object(object_schema(), field_name="schema")

    assert validator.validate_instance(instance={"value": "ok"}, schema=schema) == ()

    issues = validator.validate_instance(instance={"value": 42}, schema=schema)

    assert issues
    assert all(issue.message == "value does not satisfy the declared schema" for issue in issues)
