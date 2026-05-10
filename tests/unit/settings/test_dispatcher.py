"""Tests for SettingsChangeDispatcher."""

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, datetime

import pytest

from synthorg.communication.channel import Channel
from synthorg.communication.enums import ChannelType, MessageType
from synthorg.communication.errors import ChannelAlreadyExistsError
from synthorg.communication.message import Message, MessageMetadata, TextPart
from synthorg.communication.subscription import DeliveryEnvelope, Subscription
from synthorg.settings.dispatcher import SettingsChangeDispatcher

# ── Helpers ──────────────────────────────────────────────────────


def _settings_message(
    namespace: str,
    key: str,
    restart_required: bool = False,
) -> Message:
    """Build a #settings channel message matching SettingsService format."""
    return Message(
        timestamp=datetime.now(UTC),
        sender="system",
        to="#settings",
        type=MessageType.ANNOUNCEMENT,
        channel="#settings",
        parts=(TextPart(text=f"Setting changed: {namespace}/{key}"),),
        metadata=MessageMetadata(
            extra=(
                ("namespace", namespace),
                ("key", key),
                ("restart_required", str(restart_required)),
            ),
        ),
    )


def _envelope(msg: Message) -> DeliveryEnvelope:
    return DeliveryEnvelope(
        message=msg,
        channel_name="#settings",
        delivered_at=datetime.now(UTC),
    )


class _FakeSubscriber:
    """Test subscriber that records calls and signals completion."""

    def __init__(
        self,
        name: str,
        keys: frozenset[tuple[str, str]],
    ) -> None:
        self._name = name
        self._keys = keys
        self.calls: list[tuple[str, str]] = []
        self.notified: asyncio.Event = asyncio.Event()

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        return self._keys

    @property
    def subscriber_name(self) -> str:
        return self._name

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        self.calls.append((namespace, key))
        self.notified.set()


class _ErrorSubscriber(_FakeSubscriber):
    """Subscriber that raises on every call."""

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        msg = f"boom from {self._name}"
        raise RuntimeError(msg)


class _FakeBus:
    """Controllable message bus for dispatcher tests.

    Feed messages via ``enqueue(envelope)``; the dispatcher's polling
    loop will consume them in order.
    """

    def __init__(self) -> None:
        self._running = True
        self._queue: asyncio.Queue[DeliveryEnvelope | None] = asyncio.Queue()
        self._channels_created: list[str] = []
        self._subscriptions: list[tuple[str, str]] = []
        self._stop_event = asyncio.Event()

    @property
    def is_running(self) -> bool:
        return self._running

    async def health_check(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()

    def enqueue(self, envelope: DeliveryEnvelope) -> None:
        self._queue.put_nowait(envelope)

    async def subscribe(self, channel_name: str, subscriber_id: str) -> Subscription:
        self._subscriptions.append((channel_name, subscriber_id))
        return Subscription(
            channel_name=channel_name,
            subscriber_id=subscriber_id,
            subscribed_at=datetime.now(UTC),
        )

    async def unsubscribe(self, channel_name: str, subscriber_id: str) -> None:
        pass

    async def receive(
        self,
        channel_name: str,
        subscriber_id: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> DeliveryEnvelope | None:
        try:
            return await asyncio.wait_for(
                self._queue.get(),
                timeout=timeout,
            )
        except TimeoutError:
            return None

    async def create_channel(self, channel: Channel) -> Channel:
        self._channels_created.append(channel.name)
        return channel

    async def get_channel(self, channel_name: str) -> Channel:
        return Channel(name=channel_name, type=ChannelType.TOPIC)

    async def list_channels(self) -> tuple[Channel, ...]:
        return ()

    async def publish(
        self,
        message: Message,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        pass

    async def send_direct(
        self,
        message: Message,
        *,
        recipient: str,
        ttl_seconds: float | None = None,
    ) -> None:
        pass

    async def publish_batch(
        self,
        messages: Sequence[Message],
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        pass

    async def get_channel_history(
        self, channel_name: str, *, limit: int | None = None
    ) -> tuple[Message, ...]:
        return ()


@pytest.fixture
def bus() -> _FakeBus:
    return _FakeBus()


@pytest.fixture
def provider_sub() -> _FakeSubscriber:
    return _FakeSubscriber(
        "provider-sub",
        frozenset({("providers", "routing_strategy")}),
    )


@pytest.fixture
def memory_sub() -> _FakeSubscriber:
    return _FakeSubscriber(
        "memory-sub",
        frozenset({("memory", "backend"), ("memory", "default_level")}),
    )


@pytest.fixture
def dispatcher(
    bus: _FakeBus,
    provider_sub: _FakeSubscriber,
    memory_sub: _FakeSubscriber,
) -> SettingsChangeDispatcher:
    return SettingsChangeDispatcher(
        message_bus=bus,
        subscribers=(provider_sub, memory_sub),
    )


@pytest.fixture
async def started_dispatcher(
    dispatcher: SettingsChangeDispatcher,
) -> AsyncGenerator[SettingsChangeDispatcher]:
    """Start the dispatcher and stop it on teardown."""
    await dispatcher.start()
    yield dispatcher
    await dispatcher.stop()


async def _wait_for_subscriber(
    subscriber: _FakeSubscriber,
    *,
    timeout: float = 2.0,  # noqa: ASYNC109
) -> None:
    """Wait until the subscriber's ``on_settings_changed`` has been called.

    Event-driven: blocks on ``subscriber.notified`` rather than polling
    or sleeping, so the test wakes deterministically as soon as the
    dispatcher finishes dispatching to this subscriber.
    """
    await asyncio.wait_for(subscriber.notified.wait(), timeout=timeout)
    # Reset for the next wait
    subscriber.notified.clear()


async def _wait_for_queue_drain(
    bus: _FakeBus,
    *,
    timeout: float = 2.0,  # noqa: ASYNC109
) -> None:
    """Wait for the bus queue to empty (for negative/skip assertions).

    Used when no subscriber is expected to be called -- we wait for the
    dispatcher to consume the message from the queue, then give it a
    tick to finish the dispatch decision (skip/restart_required).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while bus._queue.qsize() > 0:
        if loop.time() > deadline:
            msg = "Queue drain timed out"
            raise TimeoutError(msg)
        await asyncio.sleep(0)
    # One extra event-loop tick for the dispatcher to finish processing
    await asyncio.sleep(0)


# ── Lifecycle Tests ──────────────────────────────────────────────


@pytest.mark.unit
class TestDispatcherLifecycle:
    async def test_start_subscribes_to_settings_channel(
        self,
        started_dispatcher: SettingsChangeDispatcher,
        bus: _FakeBus,
    ) -> None:
        assert ("#settings", "__settings_dispatcher__") in bus._subscriptions

    async def test_double_start_raises(
        self,
        started_dispatcher: SettingsChangeDispatcher,
    ) -> None:
        with pytest.raises(RuntimeError, match="already running"):
            await started_dispatcher.start()

    async def test_stop_is_idempotent(
        self,
        dispatcher: SettingsChangeDispatcher,
    ) -> None:
        await dispatcher.start()
        await dispatcher.stop()
        await dispatcher.stop()  # should not raise

    async def test_stop_without_start(
        self,
        dispatcher: SettingsChangeDispatcher,
    ) -> None:
        # Should not raise
        await dispatcher.stop()


# ── Dispatch Tests ───────────────────────────────────────────────


@pytest.mark.unit
class TestDispatchRouting:
    async def test_dispatches_to_matching_subscriber(
        self,
        started_dispatcher: SettingsChangeDispatcher,
        bus: _FakeBus,
        provider_sub: _FakeSubscriber,
    ) -> None:
        msg = _settings_message("providers", "routing_strategy")
        bus.enqueue(_envelope(msg))
        await _wait_for_subscriber(provider_sub)
        assert ("providers", "routing_strategy") in provider_sub.calls

    async def test_does_not_dispatch_to_non_matching_subscriber(
        self,
        started_dispatcher: SettingsChangeDispatcher,
        bus: _FakeBus,
        provider_sub: _FakeSubscriber,
        memory_sub: _FakeSubscriber,
    ) -> None:
        msg = _settings_message("providers", "routing_strategy")
        bus.enqueue(_envelope(msg))
        # provider_sub matches and gets called -- wait on it
        await _wait_for_subscriber(provider_sub)
        assert len(memory_sub.calls) == 0

    async def test_dispatches_to_multiple_matching_subscribers(
        self,
        bus: _FakeBus,
    ) -> None:
        sub_a = _FakeSubscriber("a", frozenset({("ns", "k")}))
        sub_b = _FakeSubscriber("b", frozenset({("ns", "k")}))
        d = SettingsChangeDispatcher(
            message_bus=bus,
            subscribers=(sub_a, sub_b),
        )
        await d.start()
        try:
            bus.enqueue(_envelope(_settings_message("ns", "k")))
            await _wait_for_subscriber(sub_b)
            assert ("ns", "k") in sub_a.calls
            assert ("ns", "k") in sub_b.calls
        finally:
            await d.stop()

    async def test_skips_restart_required_settings(
        self,
        started_dispatcher: SettingsChangeDispatcher,
        bus: _FakeBus,
        memory_sub: _FakeSubscriber,
    ) -> None:
        msg = _settings_message("memory", "backend", restart_required=True)
        bus.enqueue(_envelope(msg))
        await _wait_for_queue_drain(bus)
        assert len(memory_sub.calls) == 0

    async def test_dispatches_non_restart_required_memory_settings(
        self,
        started_dispatcher: SettingsChangeDispatcher,
        bus: _FakeBus,
        memory_sub: _FakeSubscriber,
    ) -> None:
        msg = _settings_message("memory", "default_level", restart_required=False)
        bus.enqueue(_envelope(msg))
        await _wait_for_subscriber(memory_sub)
        assert ("memory", "default_level") in memory_sub.calls


# ── Error Isolation Tests ────────────────────────────────────────


@pytest.mark.unit
class TestDispatcherErrorIsolation:
    async def test_continues_after_subscriber_error(
        self,
        bus: _FakeBus,
    ) -> None:
        """A failing subscriber does not prevent others from being notified."""
        error_sub = _ErrorSubscriber("boom", frozenset({("ns", "k")}))
        good_sub = _FakeSubscriber("ok", frozenset({("ns", "k")}))
        d = SettingsChangeDispatcher(
            message_bus=bus,
            subscribers=(error_sub, good_sub),
        )
        await d.start()
        try:
            bus.enqueue(_envelope(_settings_message("ns", "k")))
            await _wait_for_subscriber(good_sub)
            assert ("ns", "k") in good_sub.calls
        finally:
            await d.stop()

    async def test_poll_loop_survives_subscriber_error(
        self,
        bus: _FakeBus,
    ) -> None:
        """After one error, the loop keeps processing subsequent messages."""
        error_sub = _ErrorSubscriber("boom", frozenset({("ns", "k")}))
        good_sub = _FakeSubscriber("ok", frozenset({("ns", "k")}))
        d = SettingsChangeDispatcher(
            message_bus=bus,
            subscribers=(error_sub, good_sub),
        )
        await d.start()
        try:
            bus.enqueue(_envelope(_settings_message("ns", "k")))
            await _wait_for_subscriber(good_sub)
            good_sub.calls.clear()

            bus.enqueue(_envelope(_settings_message("ns", "k")))
            await _wait_for_subscriber(good_sub)
            assert ("ns", "k") in good_sub.calls
        finally:
            await d.stop()


# ── Metadata Extraction Tests ────────────────────────────────────


@pytest.mark.unit
class TestMetadataExtraction:
    async def test_ignores_message_with_missing_metadata(
        self,
        started_dispatcher: SettingsChangeDispatcher,
        bus: _FakeBus,
        provider_sub: _FakeSubscriber,
    ) -> None:
        """Messages without namespace/key in metadata are skipped."""
        msg = Message(
            timestamp=datetime.now(UTC),
            sender="system",
            to="#settings",
            type=MessageType.ANNOUNCEMENT,
            channel="#settings",
            parts=(TextPart(text="bad message"),),
            metadata=MessageMetadata(extra=()),
        )
        bus.enqueue(_envelope(msg))
        await _wait_for_queue_drain(bus)
        assert len(provider_sub.calls) == 0

    async def test_partial_metadata_namespace_only(
        self,
        started_dispatcher: SettingsChangeDispatcher,
        bus: _FakeBus,
        provider_sub: _FakeSubscriber,
    ) -> None:
        """Message with namespace but no key is skipped."""
        msg = Message(
            timestamp=datetime.now(UTC),
            sender="system",
            to="#settings",
            type=MessageType.ANNOUNCEMENT,
            channel="#settings",
            parts=(TextPart(text="partial"),),
            metadata=MessageMetadata(
                extra=(("namespace", "providers"),),
            ),
        )
        bus.enqueue(_envelope(msg))
        await _wait_for_queue_drain(bus)
        assert len(provider_sub.calls) == 0

    async def test_restart_required_defaults_to_true_when_absent(
        self,
        bus: _FakeBus,
    ) -> None:
        """Missing restart_required metadata defaults to True (fail-safe)."""
        sub = _FakeSubscriber("sub", frozenset({("ns", "k")}))
        d = SettingsChangeDispatcher(
            message_bus=bus,
            subscribers=(sub,),
        )
        # Message with namespace and key but NO restart_required field
        msg = Message(
            timestamp=datetime.now(UTC),
            sender="system",
            to="#settings",
            type=MessageType.ANNOUNCEMENT,
            channel="#settings",
            parts=(TextPart(text="no restart flag"),),
            metadata=MessageMetadata(
                extra=(("namespace", "ns"), ("key", "k")),
            ),
        )
        await d.start()
        try:
            bus.enqueue(_envelope(msg))
            await _wait_for_queue_drain(bus)
            # Fail-safe: missing restart_required treated as True → not dispatched
            assert len(sub.calls) == 0
        finally:
            await d.stop()


# ── Done Callback Tests ──────────────────────────────────────────


@pytest.mark.unit
class TestDoneCallback:
    async def test_running_flag_cleared_on_unexpected_exit(
        self,
    ) -> None:
        """_running is set to False when poll loop exits unexpectedly."""
        sub = _FakeSubscriber("sub", frozenset())

        class _ErrorBus(_FakeBus):
            async def receive(
                self,
                channel_name: str,
                subscriber_id: str,
                *,
                timeout: float | None = None,  # noqa: ASYNC109
            ) -> DeliveryEnvelope | None:
                msg = "unexpected bus error"
                raise ValueError(msg)

        err_bus = _ErrorBus()
        d = SettingsChangeDispatcher(
            message_bus=err_bus,
            subscribers=(sub,),
        )
        await d.start()
        # Wait for the task to complete deterministically
        assert d._task is not None
        with contextlib.suppress(Exception):
            await asyncio.wait_for(d._task, timeout=2.0)
        # The done callback schedules a follow-up coroutine that
        # acquires ``_lifecycle_lock`` before clearing ``_running``;
        # wait for it to finish so the assertion observes the
        # post-lock state instead of the in-flight intermediate.
        if d._post_done_tasks:
            await asyncio.gather(*d._post_done_tasks)
        assert d._running is False


@pytest.mark.unit
class TestEnsureChannel:
    async def test_start_succeeds_when_channel_already_exists(
        self,
    ) -> None:
        """Dispatcher starts cleanly even if #settings channel pre-exists."""
        sub = _FakeSubscriber("sub", frozenset())

        class _ExistingChannelBus(_FakeBus):
            async def create_channel(self, channel: Channel) -> Channel:
                raise ChannelAlreadyExistsError(channel.name)

        bus = _ExistingChannelBus()
        d = SettingsChangeDispatcher(
            message_bus=bus,
            subscribers=(sub,),
        )
        await d.start()
        try:
            assert d._running is True
        finally:
            await d.stop()


@pytest.mark.unit
class TestConsecutiveErrors:
    async def test_transient_errors_do_not_kill_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OSError/TimeoutError are tolerated below the threshold."""
        import synthorg.settings.dispatcher as _mod

        monkeypatch.setattr(_mod, "_ERROR_BACKOFF", 0.01)
        monkeypatch.setattr(_mod, "_MAX_CONSECUTIVE_ERRORS", 5)

        sub = _FakeSubscriber("sub", frozenset({("ns", "k")}))
        call_count = 0

        class _TransientBus(_FakeBus):
            async def receive(
                self,
                channel_name: str,
                subscriber_id: str,
                *,
                timeout: float | None = None,  # noqa: ASYNC109
            ) -> DeliveryEnvelope | None:
                nonlocal call_count
                call_count += 1
                if call_count <= 3:
                    msg = "transient"
                    raise OSError(msg)
                if call_count == 4:
                    # After 3 errors, return a valid message once
                    return _envelope(_settings_message("ns", "k"))
                # Then block (normal poll timeout) -- use Event
                # instead of real sleep to avoid wall-clock delay.
                await asyncio.Event().wait()
                return None

        bus = _TransientBus()
        d = SettingsChangeDispatcher(
            message_bus=bus,
            subscribers=(sub,),
        )
        await d.start()
        try:
            await _wait_for_subscriber(sub, timeout=10.0)
            assert ("ns", "k") in sub.calls
        finally:
            await d.stop()

    async def test_max_consecutive_errors_kills_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Loop exits after _MAX_CONSECUTIVE_ERRORS OSErrors."""
        import synthorg.settings.dispatcher as _mod

        monkeypatch.setattr(_mod, "_ERROR_BACKOFF", 0.01)
        monkeypatch.setattr(_mod, "_MAX_CONSECUTIVE_ERRORS", 5)

        class _PermanentErrorBus(_FakeBus):
            async def receive(
                self,
                channel_name: str,
                subscriber_id: str,
                *,
                timeout: float | None = None,  # noqa: ASYNC109
            ) -> DeliveryEnvelope | None:
                msg = "permanent"
                raise OSError(msg)

        bus = _PermanentErrorBus()
        sub = _FakeSubscriber("sub", frozenset())
        d = SettingsChangeDispatcher(
            message_bus=bus,
            subscribers=(sub,),
        )
        await d.start()
        assert d._task is not None
        with contextlib.suppress(Exception):
            await asyncio.wait_for(d._task, timeout=10.0)
        if d._post_done_tasks:
            await asyncio.gather(*d._post_done_tasks)
        assert d._running is False
