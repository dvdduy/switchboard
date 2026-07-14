"""Immutable startup registry for preinstalled tool adapters."""

import re
from collections.abc import Mapping
from types import MappingProxyType

from switchboard.application.ports.tool_adapter import ToolAdapter
from switchboard.domain.errors import DomainValidationError

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,99}$")


class StaticToolAdapterResolver:
    """Resolve a defensively copied adapter mapping that cannot change after startup."""

    def __init__(self, adapters: Mapping[str, ToolAdapter]) -> None:
        normalized: dict[str, ToolAdapter] = {}
        for raw_key, adapter in adapters.items():
            key = raw_key.strip().lower()
            if not _KEY_PATTERN.fullmatch(key):
                raise DomainValidationError("adapter registry contains an invalid key")
            if key in normalized:
                raise DomainValidationError("adapter registry contains duplicate normalized keys")
            normalized[key] = adapter
        self._adapters = MappingProxyType(normalized)

    def resolve(self, adapter_key: str) -> ToolAdapter | None:
        return self._adapters.get(adapter_key.strip().lower())
