"""Lifecycle tests for ``SharedRateLimitCoordinator``.

Verifies the canonical pattern (per ``docs/reference/lifecycle-sync.md``):
a re-``start()`` after a clean ``stop()`` works, and a ``stop()`` whose
drain exceeds the hard deadline marks the coordinator unrestartable so
the next ``start()`` refuses.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.communication.bus_protocol import MessageBus
from synthorg.integrations.errors import IntegrationLifecycleConflictError
from synthorg.integrations.rate_limiting.shared_state import SharedRateLimitCoordinator

pytestmark = pytest.mark.unit


def _make_coordinator() -> SharedRateLimitCoordinator:
    bus = MagicMock(spec=MessageBus)
    bus.subscribe = AsyncMock(spec=MessageBus.subscribe)
    bus.unsubscribe = AsyncMock(spec=MessageBus.unsubscribe)
    bus.receive = AsyncMock(spec=MessageBus.receive, return_value=None)
    return SharedRateLimitCoordinator(bus, "test-connection")


class TestSharedRateLimitCoordinatorLifecycle:
    """Canonical lifecycle pattern."""

    async def test_restart_after_clean_stop(self) -> None:
        coordinator = _make_coordinator()
        await coordinator.start()
        await coordinator.stop()
        # After a clean stop the coordinator must restart.
        await coordinator.start()
        await coordinator.stop()

    async def test_unrestartable_after_drain_timeout(self) -> None:
        """A drain that exceeds the deadline marks the coordinator unrestartable."""
        coordinator = _make_coordinator()
        coordinator._stop_drain_timeout_seconds = 0.05
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hung_loop(self: SharedRateLimitCoordinator) -> None:
            del self
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Suppress cancellation; this simulates a stuck drain.
                await release.wait()

        with patch.object(SharedRateLimitCoordinator, "_poll_loop", hung_loop):
            await coordinator.start()
            await entered.wait()
            saved_task = coordinator._task
            try:
                with pytest.raises(TimeoutError):
                    await coordinator.stop()
                assert coordinator._stop_failed is True
                assert saved_task is not None
            finally:
                release.set()
                if saved_task is not None:
                    await saved_task

        # Subsequent start must refuse.
        with pytest.raises(IntegrationLifecycleConflictError, match="unrestartable"):
            await coordinator.start()
