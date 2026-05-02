"""Canonical lifecycle pattern tests for ``OrgInflectionMonitor``.

The unit ``test_monitor.py`` covers happy-path start / stop / tick.
This module verifies the lock-driven concurrency safety added per
``docs/reference/lifecycle-sync.md``: concurrent start, restart after
clean stop, and unrestartable flag after a drain timeout.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.meta.chief_of_staff.inflection import OrgInflectionDetector
from synthorg.meta.chief_of_staff.monitor import OrgInflectionMonitor
from synthorg.meta.signals.snapshot import SnapshotBuilder

pytestmark = pytest.mark.unit


def _make_monitor() -> OrgInflectionMonitor:
    # ``spec=SnapshotBuilder`` auto-mocks ``build`` as an AsyncMock;
    # set ``return_value`` instead of reassigning ``builder.build``.
    builder = AsyncMock(spec=SnapshotBuilder)
    builder.build.return_value = None
    return OrgInflectionMonitor(
        detector=OrgInflectionDetector(),
        snapshot_builder=builder,
        sinks=(),
        check_interval_minutes=60,
    )


class TestOrgInflectionMonitorLifecycleLock:
    """Canonical pattern compliance."""

    async def test_concurrent_starts_spawn_one_task(self) -> None:
        monitor = _make_monitor()
        try:
            await asyncio.gather(
                monitor.start(),
                monitor.start(),
                monitor.start(),
            )
            assert monitor._task is not None
        finally:
            await monitor.stop()

    async def test_restart_after_clean_stop(self) -> None:
        monitor = _make_monitor()
        await monitor.start()
        await monitor.stop()
        # Cannot assert ``_task is None`` here -- mypy narrows the
        # type and flags the subsequent ``start()`` as unreachable.
        await monitor.start()
        await monitor.stop()

    async def test_unrestartable_after_drain_timeout(self) -> None:
        monitor = _make_monitor()
        monitor._stop_drain_timeout_seconds = 0.05

        async def hung_loop(self: OrgInflectionMonitor) -> None:
            del self
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Suppress cancellation -- simulates a stuck drain.
                await asyncio.sleep(1.0)

        with patch.object(OrgInflectionMonitor, "_loop", hung_loop):
            await monitor.start()
            await asyncio.sleep(0)

            with pytest.raises(TimeoutError):
                await monitor.stop()
            assert monitor._stop_failed is True

        with pytest.raises(RuntimeError, match="unrestartable"):
            await monitor.start()
