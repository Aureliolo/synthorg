"""End-to-end webhook test: bus publish -> bridge forward -> strategy call.

Verifies that ``WebhookEventBridge`` subscribes to ``#webhooks`` and
forwards events to ``ExternalTriggerStrategy.on_external_event`` with
the expected event_type and payload, using the in-process
``InMemoryMessageBus`` and a spy ceremony scheduler that yields a
spy ``ExternalTriggerStrategy``.
"""

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from synthorg.communication.bus.memory import InMemoryMessageBus
from synthorg.engine.workflow.strategies.external_trigger import (
    ExternalTriggerStrategy,
)
from synthorg.engine.workflow.webhook_bridge import WebhookEventBridge
from synthorg.integrations.webhooks.event_bus_bridge import (
    WEBHOOK_CHANNEL,
    publish_webhook_event,
)


class _SpyExternalTriggerStrategy(ExternalTriggerStrategy):
    """Real strategy subclass that records ``on_external_event`` calls.

    The parent class uses ``__slots__`` so we extend the slot tuple
    with ``calls`` and ``called`` to add our own instance attributes
    without triggering ``AttributeError``. ``called`` is an
    ``asyncio.Event`` that tests ``await`` to deterministically wait
    for a forwarded event instead of polling with ``sleep``.
    """

    __slots__ = ("called", "calls")

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[object, str, Mapping[str, Any]]] = []
        self.called: asyncio.Event = asyncio.Event()

    async def on_external_event(
        self,
        sprint: object,
        event_name: str,
        payload: Mapping[str, Any],
    ) -> None:
        self.calls.append((sprint, event_name, payload))
        self.called.set()


class _SpyCeremonyScheduler:
    """Stand-in for ``CeremonyScheduler`` returning a fixed strategy/sprint.

    Exposes a ``processed`` ``asyncio.Event`` that tests can await to
    deterministically wait for the bridge to consume a message without
    resorting to ``asyncio.sleep``-based polling.
    """

    def __init__(
        self,
        strategy: ExternalTriggerStrategy | None,
        sprint: object | None,
    ) -> None:
        self._strategy = strategy
        self._sprint = sprint
        self.processed: asyncio.Event = asyncio.Event()

    async def get_active_info(
        self,
    ) -> tuple[object | None, object | None]:
        # ``get_active_info`` is the bridge's first contact with the
        # scheduler on every delivery. Signalling here means the test
        # can wait on a message actually reaching the forwarding
        # path, even when the strategy is ``None`` and no further
        # work runs.
        self.processed.set()
        return self._strategy, self._sprint


@pytest.mark.integration
class TestWebhookFullPath:
    """End-to-end: publish to the bus, assert the bridge forwards."""

    async def test_webhook_channel_name(self) -> None:
        """Contract check: the #webhooks channel name never changes."""
        assert WEBHOOK_CHANNEL.name == "#webhooks"

    async def test_publish_triggers_on_external_event(
        self,
        memory_bus: InMemoryMessageBus,
    ) -> None:
        strategy = _SpyExternalTriggerStrategy()
        sprint = object()
        scheduler = _SpyCeremonyScheduler(strategy, sprint)
        bridge = WebhookEventBridge(
            bus=memory_bus,
            ceremony_scheduler=scheduler,  # type: ignore[arg-type]
        )
        await bridge.start()
        try:
            await publish_webhook_event(
                bus=memory_bus,
                connection_name="conn-1",
                event_type="issues.opened",
                payload={"number": 42, "title": "hello"},
            )
            # Wait deterministically for the bridge to consume the
            # message. ``strategy.called`` is ``set()`` inside the
            # real ``on_external_event`` override; if the bridge
            # wiring is broken the timeout fires instead of flaking.
            await asyncio.wait_for(strategy.called.wait(), timeout=2.0)
        finally:
            await bridge.stop()

        assert len(strategy.calls) == 1
        forwarded_sprint, event_type, payload = strategy.calls[0]
        assert forwarded_sprint is sprint
        assert event_type == "issues.opened"
        assert payload == {"number": 42, "title": "hello"}

    async def test_publish_with_no_active_strategy_is_silently_dropped(
        self,
        memory_bus: InMemoryMessageBus,
    ) -> None:
        scheduler = _SpyCeremonyScheduler(strategy=None, sprint=None)
        bridge = WebhookEventBridge(
            bus=memory_bus,
            ceremony_scheduler=scheduler,  # type: ignore[arg-type]
        )
        await bridge.start()
        try:
            await publish_webhook_event(
                bus=memory_bus,
                connection_name="conn-2",
                event_type="test",
                payload={},
            )
            # Wait deterministically for the bridge to hand the
            # message to the scheduler -- ``get_active_info`` sets
            # ``processed`` when the message arrives, no matter
            # whether the strategy is None or a real spy.
            await asyncio.wait_for(scheduler.processed.wait(), timeout=2.0)
        finally:
            await bridge.stop()
        # No crash is the success condition -- bridge must not raise
        # when there is no active sprint/strategy.
