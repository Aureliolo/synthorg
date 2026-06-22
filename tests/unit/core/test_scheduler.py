"""Unit tests for the shared AsyncCycleScheduler base."""

import asyncio
from typing import override

import pytest

from synthorg.core.scheduler import MIN_INTERVAL_SECONDS, AsyncCycleScheduler

pytestmark = pytest.mark.unit

_STARTED = "test.scheduler.started"
_STOPPED = "test.scheduler.stopped"
_FAILED = "test.scheduler.failed"


class _CountingScheduler(AsyncCycleScheduler):
    """Minimal concrete scheduler that counts cycles and signals each one."""

    def __init__(
        self,
        *,
        interval_seconds: float = 60.0,
        enabled: bool = True,
        reset_primitives_on_stop: bool = True,
    ) -> None:
        super().__init__(
            interval_seconds=interval_seconds,
            task_name="test-counting-scheduler",
            started_event=_STARTED,
            stopped_event=_STOPPED,
            failed_event=_FAILED,
            reset_primitives_on_stop=reset_primitives_on_stop,
        )
        self._enabled = enabled
        self.cycles = 0
        self.paused_logs = 0
        self.ran = asyncio.Event()

    @override
    async def _resolve_cycle_enabled(self) -> bool:
        return self._enabled

    @override
    async def _run_cycle_once(self) -> None:
        self.cycles += 1
        self.ran.set()

    @override
    def _log_cycle_paused(self) -> None:
        self.paused_logs += 1


async def test_rejects_sub_minute_interval() -> None:
    """An interval below the minimum is rejected at construction."""
    with pytest.raises(ValueError, match="interval_seconds must be"):
        _CountingScheduler(interval_seconds=MIN_INTERVAL_SECONDS - 1)


async def test_start_runs_cycle_then_stop_clean() -> None:
    """Starting runs a cycle immediately; a clean stop nulls the primitives."""
    scheduler = _CountingScheduler()
    await scheduler.start()
    await asyncio.wait_for(scheduler.ran.wait(), timeout=5.0)
    await scheduler.stop()

    assert scheduler.cycles >= 1
    # reset_primitives_on_stop default True nulls the primitives.
    assert scheduler._stop_event is None
    assert scheduler._lifecycle_lock is None


async def test_disabled_cycle_logs_paused_and_skips_work() -> None:
    """A disabled tick calls the paused hook and never runs the cycle."""
    scheduler = _CountingScheduler(enabled=False)
    await scheduler.start()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(scheduler.ran.wait(), timeout=0.3)
    await scheduler.stop()

    assert scheduler.cycles == 0
    assert scheduler.paused_logs >= 1


async def test_stop_before_start_is_noop() -> None:
    """Stopping a never-started scheduler is harmless."""
    scheduler = _CountingScheduler()
    await scheduler.stop()
    assert scheduler.cycles == 0


async def test_start_idempotent() -> None:
    """A second start while running is a no-op (one task)."""
    scheduler = _CountingScheduler()
    await scheduler.start()
    first_task = scheduler._task
    await scheduler.start()
    assert scheduler._task is first_task
    await scheduler.stop()


async def test_reset_primitives_false_keeps_binding() -> None:
    """``reset_primitives_on_stop=False`` keeps the primitives bound."""
    scheduler = _CountingScheduler(reset_primitives_on_stop=False)
    await scheduler.start()
    await asyncio.wait_for(scheduler.ran.wait(), timeout=5.0)
    await scheduler.stop()

    assert scheduler._stop_event is not None
    assert scheduler._lifecycle_lock is not None
    assert scheduler._task is None


async def test_unrestartable_after_failed_stop() -> None:
    """A scheduler marked unrestartable rejects start()."""
    scheduler = _CountingScheduler()
    scheduler._stop_failed = True
    with pytest.raises(RuntimeError, match="unrestartable"):
        await scheduler.start()


async def test_stop_drain_timeout_marks_unrestartable() -> None:
    """A never-returning cycle trips the drain deadline and blocks restart."""
    started = asyncio.Event()
    release = asyncio.Event()

    class _HangingScheduler(AsyncCycleScheduler):
        def __init__(self) -> None:
            super().__init__(
                interval_seconds=60.0,
                task_name="test-hanging-scheduler",
                started_event=_STARTED,
                stopped_event=_STOPPED,
                failed_event=_FAILED,
            )

        @override
        async def _run_cycle_once(self) -> None:
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                await release.wait()

    scheduler = _HangingScheduler()
    scheduler._drain_timeout = 0.05
    await scheduler.start()
    await asyncio.wait_for(started.wait(), timeout=5.0)
    try:
        with pytest.raises(TimeoutError):
            await scheduler.stop()
    finally:
        release.set()
    assert scheduler._stop_failed is True
    with pytest.raises(RuntimeError, match="unrestartable"):
        await scheduler.start()


async def test_run_cycle_once_required() -> None:
    """The base ``_run_cycle_once`` raises NotImplementedError unless overridden."""
    scheduler = AsyncCycleScheduler(
        interval_seconds=60.0,
        task_name="test-bare-scheduler",
        started_event=_STARTED,
        stopped_event=_STOPPED,
        failed_event=_FAILED,
    )
    with pytest.raises(NotImplementedError):
        await scheduler._run_cycle_once()
