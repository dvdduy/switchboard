"""Switchboard worker composition root and process entry point."""

import asyncio
import logging
import signal
from types import FrameType

from switchboard.bootstrap.config import load_settings
from switchboard.bootstrap.resources import build_runtime_resources
from switchboard.workers.runner import WorkerRunner

logger = logging.getLogger(__name__)


def _install_signal_handlers(runner: WorkerRunner) -> None:
    """Translate operating-system shutdown signals into a graceful stop."""

    loop = asyncio.get_running_loop()

    def handle_shutdown_signal(
        signal_number: int,
        _: FrameType | None,
    ) -> None:
        signal_name = signal.Signals(signal_number).name
        logger.info("Shutdown signal received: %s", signal_name)

        # Schedule the event mutation on the asyncio loop rather than doing
        # asynchronous work inside the synchronous signal callback.
        loop.call_soon_threadsafe(runner.request_stop)

    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)


async def run_worker() -> None:
    """Construct and run the Switchboard worker process."""

    settings = load_settings()

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    resources = build_runtime_resources(settings)
    runner = WorkerRunner()

    _install_signal_handlers(runner)

    logger.info(
        "Switchboard worker started environment=%s",
        settings.environment,
    )

    try:
        await runner.run()
    finally:
        logger.info("Switchboard worker stopping")
        await resources.close()
        logger.info("Switchboard worker stopped")


def main() -> None:
    """Run the worker as a standalone process."""

    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
