"""JSON-compatible validation, freezing, and canonical serialization."""

import json
from collections.abc import Mapping, Sequence
from math import isfinite
from types import MappingProxyType
from typing import cast

from switchboard.domain.errors import DomainValidationError

JsonObject = Mapping[str, object]


def freeze_json_value(value: object, *, path: str) -> object:
    """Validate and recursively freeze one JSON-compatible value."""

    if value is None or isinstance(value, (str, bool, int)):
        return value

    if isinstance(value, float):
        if not isfinite(value):
            raise DomainValidationError(f"{path} must contain only finite numbers")
        return value

    if isinstance(value, Mapping):
        frozen_mapping: dict[str, object] = {}
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise DomainValidationError(f"{path} object keys must be strings")
            nested_path = f"{path}.{key}" if path else key
            frozen_mapping[key] = freeze_json_value(nested_value, path=nested_path)
        return MappingProxyType(frozen_mapping)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(
            freeze_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        )

    raise DomainValidationError(f"{path} contains unsupported JSON value {type(value).__name__}")


def freeze_json_object(value: Mapping[str, object], *, field_name: str) -> JsonObject:
    """Validate and recursively freeze a JSON object."""

    return cast(JsonObject, freeze_json_value(value, path=field_name))


def mutable_json_value(value: object) -> object:
    """Convert immutable domain JSON to serializer-friendly containers."""

    if isinstance(value, Mapping):
        return {key: mutable_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [mutable_json_value(item) for item in value]
    return value


def canonical_json(value: object) -> str:
    """Serialize JSON-compatible content deterministically."""

    return json.dumps(
        mutable_json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
