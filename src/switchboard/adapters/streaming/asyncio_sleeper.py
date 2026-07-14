"""Asyncio implementation of the application polling sleeper port."""

import asyncio


class AsyncioSleeper:
    """Suspend polling with the process event loop."""

    async def sleep(self, delay_seconds: float) -> None:
        """Wait without blocking other observers."""

        await asyncio.sleep(delay_seconds)
