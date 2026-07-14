"""Port for suspending polling without coupling to an async runtime."""

from typing import Protocol


class AsyncSleeper(Protocol):
    """Suspend one observer for a requested duration."""

    async def sleep(self, delay_seconds: float) -> None:
        """Wait without blocking the event loop."""
