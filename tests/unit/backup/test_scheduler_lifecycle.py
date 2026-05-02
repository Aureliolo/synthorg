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
        try:
            await asyncio.gather(
                scheduler.start(),
                scheduler.start(),
                scheduler.start(),
            )
            assert scheduler._task is not None
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

        async def hung_loop(self: BackupScheduler) -> None:
            del self
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(1.0)

        with patch.object(BackupScheduler, "_run_loop", hung_loop):
            await scheduler.start()
            await asyncio.sleep(0)

            with pytest.raises(TimeoutError):
                await scheduler.stop()
            assert scheduler._stop_failed is True

        with pytest.raises(RuntimeError, match="unrestartable"):
            await scheduler.start()
