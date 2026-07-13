"""Ports for checking external dependency availability."""

from typing import Protocol


class HealthProbe(Protocol):
    """Checks whether one external dependency is available."""

    name: str

    async def check(self) -> None:
        """Raise an exception when the dependency is unavailable."""
