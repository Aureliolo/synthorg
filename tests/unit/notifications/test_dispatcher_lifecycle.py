"""Lifecycle tests for ``NotificationDispatcher``.

The dispatcher has no background loop; its drain is the in-flight
``dispatch()`` wait inside ``aclose()``. Verifies the canonical pattern
(per ``docs/reference/lifecycle-sync.md``): a re-``start()`` after a
clean ``aclose()`` works, and an ``aclose()`` whose in-flight drain
exceeds the hard deadline marks the dispatcher unrestartable so the next
``start()`` refuses.
"""

import asyncio

import pytest

from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.errors import NotificationDispatcherUnrestartableError

pytestmark = pytest.mark.unit


class TestNotificationDispatcherLifecycle:
    """Canonical lifecycle pattern."""

    async def test_restart_after_clean_close(self) -> None:
        dispatcher = NotificationDispatcher()
        await dispatcher.start()
        await dispatcher.aclose()
        # After a clean close the dispatcher must restart.
        await dispatcher.start()
        await dispatcher.aclose()

    async def test_unrestartable_after_drain_timeout(self) -> None:
        """A stuck in-flight dispatch trips the drain deadline."""
        dispatcher = NotificationDispatcher()
        dispatcher._stop_drain_timeout_seconds = 0.05
        await dispatcher.start()
        # Simulate a dispatch that never finishes: increment the
        # in-flight counter and clear the idle event so the aclose()
        # drain blocks until the deadline.
        dispatcher._dispatch_inflight += 1
        dispatcher._dispatch_idle.clear()
        try:
            with pytest.raises(TimeoutError):
                await dispatcher.aclose()
            assert dispatcher._stop_failed is True
        finally:
            # Release the abandoned shielded waiter so it does not leak
            # past the test as a pending task.
            dispatcher._dispatch_idle.set()
            await asyncio.sleep(0)

        with pytest.raises(
            NotificationDispatcherUnrestartableError, match="unrestartable"
        ):
            await dispatcher.start()
