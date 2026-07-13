import asyncio

import pytest

from switchboard.workers.runner import WorkerRunner


def test_worker_rejects_non_positive_idle_interval() -> None:
    with pytest.raises(
        ValueError,
        match="idle_interval_seconds must be greater than zero",
    ):
        WorkerRunner(idle_interval_seconds=0)


async def test_worker_remains_active_until_stop_is_requested() -> None:
    runner = WorkerRunner(idle_interval_seconds=0.01)

    worker_task = asyncio.create_task(runner.run())

    # Yield control so the worker task enters its loop.
    await asyncio.sleep(0)

    assert worker_task.done() is False
    assert runner.is_stop_requested is False

    runner.request_stop()

    await asyncio.wait_for(worker_task, timeout=0.5)

    assert worker_task.done() is True
    assert runner.is_stop_requested is True


async def test_stop_can_be_requested_before_worker_starts() -> None:
    runner = WorkerRunner(idle_interval_seconds=0.01)
    runner.request_stop()

    await asyncio.wait_for(runner.run(), timeout=0.5)

    assert runner.is_stop_requested is True
