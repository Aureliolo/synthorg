"""TOCTOU race tests for MessageBusBridge.start.

The un-synchronized ``start()`` at ``api/bus_bridge.py:148-187`` checks
``_running`` then sets it, and then iterates the channel list to
``await self._bus.subscribe(...)`` and ``self._tasks.append(...)``.
Two concurrent starts can both pass the check, both flip ``_running``,
and both subscribe + spawn polling tasks for every channel,
duplicating work and leaking tasks.

The fix wraps the whole body in ``self._lifecycle_lock`` so the
check-and-set is serialized.
"""

import asyncio

import pytest
from litestar.channels import ChannelsPlugin
from litestar.channels.backends.memory import MemoryChannelsBackend

from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.api.channels import ALL_CHANNELS

from .conftest import FakeMessageBus


@pytest.mark.unit
class TestConcurrentStart:
    """Two concurrent ``start()`` calls must be serialized."""

    async def test_concurrent_start_is_serialized(self) -> None:
        """Exactly one ``start()`` wins; the loser raises ``RuntimeError``.

        Under the un-synchronized implementation two concurrent starts
        would both observe ``_running=False`` and both call
        ``self._bus.subscribe(channel, ...)`` for every channel, and
        both would spawn a polling task per channel -- visible as 2x
        subscribe calls and 2x tasks.

        The fix serializes the whole start body under
        ``_lifecycle_lock``. Invariants:

        - exactly one of the two starts returns successfully
        - exactly one raises ``RuntimeError``
        - ``bus.subscribe`` is called exactly ``len(ALL_CHANNELS)`` times
        - ``bridge._tasks`` has exactly ``len(ALL_CHANNELS)`` tasks
        """
        bus = FakeMessageBus()
        await bus.start()
        plugin = ChannelsPlugin(
            backend=MemoryChannelsBackend(history=5),
            channels=ALL_CHANNELS,
        )
        bridge = MessageBusBridge(bus, plugin)

        # Wrap subscribe with a small sleep so the first task gives up
        # the event loop mid-iteration -- forces the second start() to
        # observe the check-and-set interleaving.
        original_subscribe = bus.subscribe
        subscribe_calls: list[tuple[str, str]] = []

        async def _instrumented_subscribe(channel: str, subscriber: str) -> object:
            subscribe_calls.append((channel, subscriber))
            await asyncio.sleep(0)
            return await original_subscribe(channel, subscriber)

        bus.subscribe = _instrumented_subscribe  # type: ignore[method-assign,assignment]

        results = await asyncio.gather(
            bridge.start(),
            bridge.start(),
            return_exceptions=True,
        )
        try:
            successes = [r for r in results if not isinstance(r, BaseException)]
            failures = [r for r in results if isinstance(r, RuntimeError)]
            assert len(successes) == 1, f"expected one success, got {results!r}"
            assert len(failures) == 1, f"expected one RuntimeError, got {results!r}"
            assert len(subscribe_calls) == len(ALL_CHANNELS), (
                f"expected {len(ALL_CHANNELS)} subscribes, "
                f"got {len(subscribe_calls)}: {subscribe_calls!r}"
            )
            assert len(bridge._tasks) == len(ALL_CHANNELS), (
                f"expected one task per channel, got {len(bridge._tasks)}"
            )
        finally:
            await bridge.stop()
