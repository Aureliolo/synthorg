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
from synthorg.settings.resolver import ConfigResolver

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


class TestResolveEnabled:
    """``_resolve_enabled`` reads a live kill-switch and throttles warnings.

    Without throttling a prolonged settings-resolver outage would log a
    warning every iteration, drowning higher-signal errors in the same
    sink. The log-once-until-recovery pattern is the canonical guard
    used elsewhere in the bridge (``_get_poll_timeout`` /
    ``_get_max_consecutive_errors``).
    """

    @staticmethod
    def _make_bridge(resolver: ConfigResolver | None) -> WebhookEventBridge:
        bus = AsyncMock(spec=MessageBus)
        scheduler = AsyncMock(spec=CeremonyScheduler)
        return WebhookEventBridge(
            bus=bus,
            ceremony_scheduler=scheduler,
            config_resolver=resolver,
        )

    async def test_no_resolver_returns_true(self) -> None:
        """Fail-safe: without a resolver the bridge defaults to enabled."""
        bridge = self._make_bridge(None)
        assert await bridge._resolve_enabled() is True

    async def test_returns_resolver_value_and_clears_throttle(self) -> None:
        """A successful resolver read returns the value and clears the flag."""
        # Drive the throttle on via a fake outage first so the
        # recovery path is exercised. Setting the bool attribute
        # directly would let mypy narrow it to ``Literal[True]`` and
        # the later "cleared" assertion would be flagged as
        # unreachable, hiding the actual recovery semantics.
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.side_effect = RuntimeError("transient failure")
        bridge = self._make_bridge(resolver)
        assert (await bridge._resolve_enabled()) is True
        flag_after_outage: bool = bridge._enabled_fallback_logged
        assert flag_after_outage is True

        resolver.get_bool.side_effect = None
        resolver.get_bool.return_value = False
        assert (await bridge._resolve_enabled()) is False
        flag_after_recovery: bool = bridge._enabled_fallback_logged
        assert flag_after_recovery is False

    async def test_resolver_outage_logs_once_until_recovery(self) -> None:
        """Repeat outages skip the warning until a successful read."""
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.side_effect = RuntimeError("settings backend down")
        bridge = self._make_bridge(resolver)

        with patch("synthorg.engine.workflow.webhook_bridge.logger") as patched_logger:
            assert (await bridge._resolve_enabled()) is True
            assert (await bridge._resolve_enabled()) is True
            assert (await bridge._resolve_enabled()) is True
            # Three iterations, exactly one warning -- the throttle is on.
            assert patched_logger.warning.call_count == 1
            flag_after_outage: bool = bridge._enabled_fallback_logged
            assert flag_after_outage is True

            # Recovery: a successful read clears the flag so a later
            # outage surfaces a fresh warning.
            resolver.get_bool.side_effect = None
            resolver.get_bool.return_value = True
            assert (await bridge._resolve_enabled()) is True
            flag_after_recovery: bool = bridge._enabled_fallback_logged
            assert flag_after_recovery is False

            resolver.get_bool.side_effect = RuntimeError("settings backend down")
            resolver.get_bool.return_value = None
            assert (await bridge._resolve_enabled()) is True
            assert patched_logger.warning.call_count == 2

    async def test_set_config_resolver_late_binds(self) -> None:
        """The lifecycle hook can rebind the resolver after construction."""
        bridge = self._make_bridge(None)
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = False
        bridge.set_config_resolver(resolver)

        assert (await bridge._resolve_enabled()) is False
        resolver.get_bool.assert_awaited_once()
