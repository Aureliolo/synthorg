"""Tests for message bus bridge."""

import asyncio
import contextlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.api.ws_models import WsEventType
from synthorg.communication.enums import MessagePriority, MessageType
from synthorg.communication.message import Message
from synthorg.settings.resolver import ConfigResolver


@pytest.mark.unit
class TestMessageBusBridge:
    def test_to_ws_event_conversion(self) -> None:
        msg = Message.model_validate(
            {
                "from": "alice",
                "to": "bob",
                "channel": "general",
                "parts": [{"type": "text", "text": "Hello!"}],
                "type": MessageType.TASK_UPDATE,
                "priority": MessagePriority.NORMAL,
                "timestamp": datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
            }
        )
        event = MessageBusBridge._to_ws_event(msg, "messages")
        assert event.event_type == WsEventType.MESSAGE_SENT
        assert event.channel == "messages"
        assert event.payload["sender"] == "alice"
        assert event.payload["content"] == "Hello!"

    async def test_resolve_enabled_no_resolver_returns_true(self) -> None:
        """Fail-safe: without a resolver the bridge defaults to enabled."""
        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        bridge = MessageBusBridge(bus, plugin)
        assert (await bridge._resolve_enabled()) is True

    async def test_resolve_enabled_returns_resolver_value(self) -> None:
        """The resolver value flows through unchanged when resolution succeeds."""
        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = False
        bridge = MessageBusBridge(bus, plugin, config_resolver=resolver)

        assert (await bridge._resolve_enabled()) is False
        flag: bool = bridge._enabled_fallback_logged
        assert flag is False

    async def test_resolve_enabled_outage_throttles_warnings(self) -> None:
        """A prolonged resolver outage logs once until recovery."""
        from unittest.mock import patch

        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.side_effect = RuntimeError("settings backend down")
        bridge = MessageBusBridge(bus, plugin, config_resolver=resolver)

        with patch("synthorg.api.bus_bridge.logger") as patched_logger:
            assert (await bridge._resolve_enabled()) is True
            assert (await bridge._resolve_enabled()) is True
            assert (await bridge._resolve_enabled()) is True
            assert patched_logger.warning.call_count == 1
            flag_during_outage: bool = bridge._enabled_fallback_logged
            assert flag_during_outage is True

            resolver.get_bool.side_effect = None
            resolver.get_bool.return_value = True
            assert (await bridge._resolve_enabled()) is True
            flag_after_recovery: bool = bridge._enabled_fallback_logged
            assert flag_after_recovery is False

    async def test_set_config_resolver_late_binds(self) -> None:
        """Lifecycle hook can rebind the resolver after construction.

        On the auto-wire startup path the resolver is not available
        when the bridge is constructed, so the constructor captures
        ``None``. The startup hook then calls this setter once the
        resolver is wired so subsequent ``_get_poll_timeout`` /
        ``_get_max_consecutive_errors`` / ``_get_stop_drain_timeout``
        reads honour the live operator-tuned values.
        """
        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        bridge = MessageBusBridge(bus, plugin)
        # Read through a local so mypy does not narrow
        # ``bridge._config_resolver`` to ``None`` for the rest of the
        # function (which would flag the post-rebind ``is resolver``
        # check as unreachable).
        eager_resolver: ConfigResolver | None = bridge._config_resolver
        assert eager_resolver is None

        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_float.return_value = 7.5
        bridge.set_config_resolver(resolver)
        rebound_resolver: ConfigResolver | None = bridge._config_resolver
        assert rebound_resolver is resolver

        # The poll-timeout helper now consults the live resolver.
        assert (await bridge._get_poll_timeout()) == 7.5
        resolver.get_float.assert_awaited()

    async def test_poll_channel_skips_bus_when_disabled(self) -> None:
        """``_poll_channel`` consults the kill-switch BEFORE ``bus.receive``.

        Pins the order of operations: if ``communication.bus_bridge_enabled``
        is False, the loop must short-circuit before consuming a bus
        message and before publishing to the Litestar channel. Without
        this regression test, a future refactor that moves the
        ``_resolve_enabled`` check below the ``receive`` call would
        let the bridge keep draining messages while disabled, and the
        existing helper-level tests would still pass.
        """
        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        bus.receive = AsyncMock(spec=FakeMessageBus.receive, return_value=None)  # type: ignore[method-assign]
        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        plugin.publish = MagicMock(spec=ChannelsPlugin.publish)  # type: ignore[method-assign]

        resolver = AsyncMock(spec=ConfigResolver)
        # Kill-switch off; first iteration should short-circuit, then
        # we cancel before the next sleep completes.
        resolver.get_bool.return_value = False
        # ``_get_poll_timeout`` is called before the kill-switch check;
        # resolver.get_float may be awaited too. A tiny value keeps the
        # paused-branch ``await asyncio.sleep`` short while we cancel.
        resolver.get_float.return_value = 0.001
        bridge = MessageBusBridge(bus, plugin, config_resolver=resolver)

        task = asyncio.create_task(bridge._poll_channel("messages"))
        try:
            # Wait until the kill-switch lookup has fired at least once,
            # then cancel. ``asyncio.wait_for(get_bool wait)`` would be
            # cleaner but ``get_bool`` is an AsyncMock without a
            # synchronous ``called`` event; sleep(0) yields the loop
            # enough times for the first iteration to enter the paused
            # branch.
            for _ in range(20):
                await asyncio.sleep(0)
                if resolver.get_bool.await_count > 0:
                    break
            assert resolver.get_bool.await_count >= 1, (
                "kill-switch must be consulted at least once"
            )
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        bus.receive.assert_not_called()
        plugin.publish.assert_not_called()

    def test_to_ws_event_has_timestamp(self) -> None:
        msg = Message.model_validate(
            {
                "from": "alice",
                "to": "bob",
                "channel": "general",
                "parts": [{"type": "text", "text": "Test"}],
                "type": MessageType.TASK_UPDATE,
                "priority": MessagePriority.NORMAL,
                "timestamp": datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
            }
        )
        event = MessageBusBridge._to_ws_event(msg, "tasks")
        assert event.timestamp is not None


@pytest.mark.unit
class TestBridgeLifecycle:
    async def test_start_creates_tasks(self) -> None:
        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        await bus.start()
        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        bridge = MessageBusBridge(bus, plugin)
        await bridge.start()
        assert bridge._running is True
        assert len(bridge._tasks) > 0
        await bridge.stop()

    async def test_double_start_raises(self) -> None:
        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        await bus.start()
        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        bridge = MessageBusBridge(bus, plugin)
        await bridge.start()
        with pytest.raises(RuntimeError, match="already running"):
            await bridge.start()
        await bridge.stop()

    async def test_stop_cancels_tasks(self) -> None:
        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        await bus.start()
        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        bridge = MessageBusBridge(bus, plugin)
        await bridge.start()
        await bridge.stop()
        assert bridge._running is False
        assert len(bridge._tasks) == 0

    async def test_start_zero_channels_raises(self) -> None:
        """If all subscriptions fail, bridge should raise."""
        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        await bus.start()

        # Make subscribe always fail
        async def failing_subscribe(channel_name: str, subscriber_id: str) -> None:
            msg = "sub fail"
            raise OSError(msg)

        bus.subscribe = failing_subscribe  # type: ignore[method-assign]

        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        bridge = MessageBusBridge(bus, plugin)
        with pytest.raises(RuntimeError, match="failed to subscribe"):
            await bridge.start()


@pytest.mark.unit
class TestPollChannel:
    async def test_circuit_breaker_after_max_errors(self) -> None:
        """Polling stops after _MAX_CONSECUTIVE_ERRORS failures."""
        from unittest.mock import patch

        from litestar.channels import ChannelsPlugin
        from litestar.channels.backends.memory import MemoryChannelsBackend

        from synthorg.api.bus_bridge import _MAX_CONSECUTIVE_ERRORS
        from synthorg.api.channels import ALL_CHANNELS
        from tests.unit.api.conftest import FakeMessageBus

        bus = FakeMessageBus()
        await bus.start()

        call_count = 0

        async def failing_receive(
            channel_name: str,
            subscriber_id: str,
            *,
            timeout: float | None = None,  # noqa: ASYNC109
        ) -> None:
            nonlocal call_count
            call_count += 1
            msg = "connection lost"
            raise OSError(msg)

        bus.receive = failing_receive  # type: ignore[method-assign]

        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        bridge = MessageBusBridge(bus, plugin)
        # Patch _POLL_TIMEOUT to 0 so sleeps between errors are instant
        with patch("synthorg.api.bus_bridge._POLL_TIMEOUT", 0.0):
            await bridge._poll_channel("tasks")
        assert call_count >= _MAX_CONSECUTIVE_ERRORS
