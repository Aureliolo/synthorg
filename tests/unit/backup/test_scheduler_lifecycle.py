"""Canonical lifecycle pattern tests for ``BackupScheduler``.

The unit ``test_scheduler.py`` covers happy-path start / stop / loop
behaviour. This module verifies the lock-driven concurrency safety
added per ``docs/reference/lifecycle-sync.md``: concurrent start,
restart after clean stop, and unrestartable flag after a drain
timeout.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.backup.errors import BackupUnrestartableError
from synthorg.backup.scheduler import BackupScheduler
from synthorg.backup.service import BackupService

pytestmark = pytest.mark.unit


def _make_scheduler() -> BackupScheduler:
    service = MagicMock(spec=BackupService)
    service.create_backup = AsyncMock(spec=BackupService.create_backup)
    return BackupScheduler(service, interval_hours=1)


class TestBackupSchedulerLifecycleLock:
    """Canonical pattern compliance."""

    async def test_concurrent_starts_spawn_one_task(self) -> None:
        scheduler = _make_scheduler()
        original_create_task = asyncio.create_task
        spawned: list[asyncio.Task[object]] = []

        def _counting_create_task(
            coro: object,
            **kwargs: object,
        ) -> asyncio.Task[object]:
            task: asyncio.Task[object] = original_create_task(coro, **kwargs)  # type: ignore[arg-type]
            spawned.append(task)
            return task

        try:
            with patch(
                "synthorg.backup.scheduler.asyncio.create_task",
                _counting_create_task,
            ):
                await asyncio.gather(
                    scheduler.start(),
                    scheduler.start(),
                    scheduler.start(),
                )
            assert scheduler._task is not None
            # Exactly one spawn under three concurrent ``start()`` calls
            # is the canonical lifecycle contract; ``_task is not None``
            # alone would also pass if the lock leaked and the same task
            # got re-assigned each time.
            assert len(spawned) == 1
        finally:
            await scheduler.stop()

    async def test_restart_after_clean_stop(self) -> None:
        scheduler = _make_scheduler()
        await scheduler.start()
        await scheduler.stop()
        assert scheduler._task is None
        await scheduler.start()
        assert scheduler.is_running
        await scheduler.stop()

    async def test_unrestartable_after_drain_timeout(self) -> None:
        scheduler = _make_scheduler()
        scheduler._stop_drain_timeout_seconds = 0.05
        # ``release`` lets the test wake the patched loop after the
        # timeout assertion. Without it the suppressed-cancel branch
        # would block on a wall-clock sleep and leak a pending task
        # past the patch scope; later tests could then observe a
        # ``_task`` that does not belong to them.
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hung_loop(self: BackupScheduler) -> None:
            del self
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()

        with patch.object(BackupScheduler, "_run_loop", hung_loop):
            await scheduler.start()
            await entered.wait()
            saved_task = scheduler._task
            try:
                with pytest.raises(TimeoutError):
                    await scheduler.stop()
                assert scheduler._stop_failed is True
                assert saved_task is not None
            finally:
                # ``finally`` so a failed assertion above still
                # releases the hung loop and drains the orphan task.
                release.set()
                if saved_task is not None:
                    await saved_task

        with pytest.raises(BackupUnrestartableError, match="unrestartable"):
            await scheduler.start()
