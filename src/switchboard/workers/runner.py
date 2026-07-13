"""Long running Switchboard worker loop."""

import asyncio


class WorkerRunner:
    """Runs until graceful shutdown is requested."""

    def __init__(self, *, idle_interval_seconds: float = 1.0) -> None:
        if idle_interval_seconds <= 0:
            raise ValueError("idle_interval_seconds must be greater than zero")

        self._idle_interval_seconds = idle_interval_seconds
        self._stop_event = asyncio.Event()

    @property
    def is_stop_requested(self) -> bool:
        """Return whether shutdown has been requested."""

        return self._stop_event.is_set()

    def request_stop(self) -> None:
        """Request graceful worker shutdown."""

        self._stop_event.set()

    async def run(self) -> None:
        """Remain active until shutdown is requested."""

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._idle_interval_seconds,
                )
            except TimeoutError:
                # No durable job source exists yet. Future checkpoints will
                # replace this idle wait with bounded job polling.
                continue
