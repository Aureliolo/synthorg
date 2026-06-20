"""Lifecycle tests for ``WebhookEventBridge``.

Verifies the canonical pattern (per ``docs/reference/lifecycle-sync.md``):
a re-``start()`` after a clean ``stop()`` works, and a ``stop()`` whose
drain exceeds the hard deadline marks the bridge unrestartable so the
next ``start()`` refuses.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.communication.bus_protocol import MessageBus
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.engine.workflow.errors import WebhookBridgeUnrestartableError
from synthorg.engine.workflow.webhook_bridge import WebhookEventBridge

pytestmark = pytest.mark.unit


def _make_bridge() -> WebhookEventBridge:
    bus = MagicMock(spec=MessageBus)
    bus.subscribe = AsyncMock(spec=MessageBus.subscribe)
    bus.unsubscribe = AsyncMock(spec=MessageBus.unsubscribe)
    scheduler = MagicMock(spec=CeremonyScheduler)
    return WebhookEventBridge(bus, scheduler)


class TestWebhookEventBridgeLifecycle:
    """Canonical lifecycle pattern."""

    async def test_restart_after_clean_stop(self) -> None:
        bridge = _make_bridge()
        await bridge.start()
        await bridge.stop()
        # After a clean stop the bridge must restart.
        await bridge.start()
        await bridge.stop()

    async def test_unrestartable_after_drain_timeout(self) -> None:
        """A drain that exceeds the deadline marks the bridge unrestartable."""
        bridge = _make_bridge()
        bridge._stop_drain_timeout_seconds = 0.05
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hung_loop(self: WebhookEventBridge) -> None:
            del self
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()

        with patch.object(WebhookEventBridge, "_poll_loop", hung_loop):
            await bridge.start()
            await entered.wait()
            saved_task = bridge._task
            try:
                with pytest.raises(TimeoutError):
                    await bridge.stop()
                assert bridge._stop_failed is True
                assert saved_task is not None
            finally:
                release.set()
                if saved_task is not None:
                    await saved_task

        with pytest.raises(WebhookBridgeUnrestartableError, match="unrestartable"):
            await bridge.start()
