"""Application service for determining runtime readiness."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from switchboard.application.ports.health import HealthProbe

DependencyAvailability = Literal["available", "unavailable"]


@dataclass(frozen=True)
class ReadinessResult:
    """Availability result for all required dependencies."""

    dependencies: dict[str, DependencyAvailability]

    @property
    def is_ready(self) -> bool:
        """Return whether every dependency is available."""

        return all(availability == "available" for availability in self.dependencies.values())


class ReadinessService:
    """Checks required dependencies concurrently."""

    def __init__(self, probes: Sequence[HealthProbe]) -> None:
        self._probes = tuple(probes)

    async def check(self) -> ReadinessResult:
        """Check every configured dependency."""

        results = await asyncio.gather(*(self._check_probe(probe) for probe in self._probes))

        return ReadinessResult(dependencies=dict(results))

    @staticmethod
    async def _check_probe(
        probe: HealthProbe,
    ) -> tuple[str, DependencyAvailability]:
        try:
            await probe.check()
        except Exception:
            return probe.name, "unavailable"

        return probe.name, "available"
