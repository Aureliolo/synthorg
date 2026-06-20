"""Lifecycle tests for ``QuotaPoller``.

Verifies the canonical pattern (per ``docs/reference/lifecycle-sync.md``):
a re-``start()`` after a clean ``stop()`` works, and a ``stop()`` whose
drain exceeds the hard deadline marks the poller unrestartable so the
next ``start()`` refuses.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.budget.errors import QuotaPollerUnrestartableError
from synthorg.budget.quota_poller import QuotaPoller
from synthorg.budget.quota_poller_config import QuotaPollerConfig
from synthorg.budget.quota_tracker import QuotaTracker

pytestmark = pytest.mark.unit


def _make_poller() -> QuotaPoller:
    tracker = AsyncMock(spec=QuotaTracker)
    tracker.get_all_snapshots = AsyncMock(
        spec=QuotaTracker.get_all_snapshots, return_value={}
    )
    return QuotaPoller(quota_tracker=tracker, config=QuotaPollerConfig(enabled=True))


class TestQuotaPollerLifecycle:
    """Canonical lifecycle pattern."""

    async def test_restart_after_clean_stop(self) -> None:
        poller = _make_poller()
        await poller.start()
        await poller.stop()
        # After a clean stop the poller must restart.
        await poller.start()
        await poller.stop()

    async def test_unrestartable_after_drain_timeout(self) -> None:
        """A drain that exceeds the deadline marks the poller unrestartable."""
        poller = _make_poller()
        poller._stop_drain_timeout_seconds = 0.05
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hung_loop(self: QuotaPoller) -> None:
            del self
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()

        with patch.object(QuotaPoller, "_poll_loop", hung_loop):
            await poller.start()
            await entered.wait()
            saved_task = poller._task
            try:
                with pytest.raises(TimeoutError):
                    await poller.stop()
                assert poller._stop_failed is True
                assert saved_task is not None
            finally:
                release.set()
                if saved_task is not None:
                    await saved_task

        with pytest.raises(QuotaPollerUnrestartableError, match="unrestartable"):
            await poller.start()
