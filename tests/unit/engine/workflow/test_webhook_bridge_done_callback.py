"""Done-callback regression for ``WebhookEventBridge._poll_loop``.

The poll loop re-raises ``MemoryError`` / ``RecursionError`` so the
task ends with a system-class exception instead of looping past it.
Without ``add_done_callback(log_task_exceptions(...))``, that
exception stays buffered on the task object and the process never
notices -- the bridge silently dies under OOM. This test pins the
chain end-to-end: ``MemoryError`` raised inside the loop fires the
``log_task_exceptions`` callback (which calls
``loop.call_exception_handler`` and logs at CRITICAL).
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.communication.bus_protocol import MessageBus
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.engine.workflow.webhook_bridge import WebhookEventBridge

pytestmark = pytest.mark.unit


async def test_poll_loop_registers_log_task_exceptions_callback() -> None:
    """``start()`` registers the canonical done-callback factory."""
    bus = AsyncMock(spec=MessageBus)
    # Receive blocks indefinitely (returns None) so the task stays
    # alive while we introspect the callback registration.
    bus.receive.return_value = None
    scheduler = AsyncMock(spec=CeremonyScheduler)

    bridge = WebhookEventBridge(bus=bus, ceremony_scheduler=scheduler)

    # ``add_done_callback`` requires a plain sync callable; a function
    # sentinel is enough here -- we only assert the factory was
    # invoked with the expected kwargs.
    def _sentinel(_task: asyncio.Task[None]) -> None:
        return

    with patch(
        "synthorg.engine.workflow.webhook_bridge.log_task_exceptions",
        return_value=_sentinel,
    ) as patched:
        await bridge.start()
        try:
            assert patched.called, "log_task_exceptions factory not invoked"
            kwargs = patched.call_args.kwargs
            assert kwargs.get("subscriber_id") == "__webhook_bridge__"
            assert kwargs.get("channel") == "#webhooks"
        finally:
            await bridge.stop()


async def test_memory_error_in_poll_loop_invokes_done_callback() -> None:
    """``MemoryError`` raised in the poll body fires the registered callback."""
    bus = AsyncMock(spec=MessageBus)
    bus.receive.side_effect = MemoryError("synthetic OOM for test")
    scheduler = AsyncMock(spec=CeremonyScheduler)

    bridge = WebhookEventBridge(bus=bus, ceremony_scheduler=scheduler)

    fired_with: list[asyncio.Task[None]] = []
    callback_fired = asyncio.Event()

    def fake_factory(*args: object, **kwargs: object) -> object:
        del args, kwargs

        def _on_done(task: asyncio.Task[None]) -> None:
            fired_with.append(task)
            callback_fired.set()

        return _on_done

    with patch(
        "synthorg.engine.workflow.webhook_bridge.log_task_exceptions",
        side_effect=fake_factory,
    ):
        await bridge.start()
        # Wait for the done-callback to fire. Done-callbacks are
        # scheduled via ``call_soon`` after the task completes, so
        # they run on a subsequent loop iteration; the Event lets us
        # await the signal directly instead of polling.
        await asyncio.wait_for(callback_fired.wait(), timeout=2.0)

        task = bridge._task
        assert task is not None
        assert task.done()
        with contextlib.suppress(MemoryError):
            task.result()
        exc = task.exception()
        assert isinstance(exc, MemoryError)
        assert task in fired_with, "done-callback was registered but never invoked"
