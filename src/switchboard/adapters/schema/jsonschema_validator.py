"""Draft 2020-12 validation through the jsonschema library."""

from collections.abc import Iterable
from typing import cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from switchboard.application.ports.json_schema import JsonSchemaIssue
from switchboard.domain.json_values import JsonObject, mutable_json_value


def _issues(
    errors: Iterable[ValidationError],
    *,
    message: str,
) -> tuple[JsonSchemaIssue, ...]:
    paths = {tuple(error.absolute_path) for error in errors}
    return tuple(
        JsonSchemaIssue(path=path, message=message)
        for path in sorted(
            paths,
            key=lambda item: tuple((isinstance(segment, int), str(segment)) for segment in item),
        )
    )


class Draft202012JsonSchemaValidator:
    """Validate schemas and instances without leaking rejected values."""

    def validate_schema(self, schema: JsonObject) -> tuple[JsonSchemaIssue, ...]:
        mutable_schema = cast(dict[str, object], mutable_json_value(schema))
        validator = Draft202012Validator(Draft202012Validator.META_SCHEMA)
        return _issues(
            validator.iter_errors(mutable_schema),
            message="schema does not satisfy Draft 2020-12",
        )

    def validate_instance(
        self,
        *,
        instance: object,
        schema: JsonObject,
    ) -> tuple[JsonSchemaIssue, ...]:
        validator = Draft202012Validator(cast(dict[str, object], mutable_json_value(schema)))
        return _issues(
            validator.iter_errors(mutable_json_value(instance)),
            message="value does not satisfy the declared schema",
        )
