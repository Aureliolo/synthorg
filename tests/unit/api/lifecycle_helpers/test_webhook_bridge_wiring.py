"""The webhook bridge comes up with the ceremony scheduler it forwards into.

It was built during construction, guarded on a ceremony scheduler that is
always absent there. So a deployment with webhooks configured, verified and
delivering forwarded none of them into a sprint's external-trigger strategy,
and nothing said so.
"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.lifecycle_helpers.webhook_bridge_wiring import (
    wire_webhook_event_bridge,
)
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.communication.bus_protocol import MessageBus
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.engine.workflow.webhook_bridge import WebhookEventBridge
from synthorg.integrations.state import IntegrationsStateSlice
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


def _bus() -> MessageBus:
    """Return a bus double the bridge can subscribe against.

    Returns:
        A ``MessageBus`` substitute.
    """
    bus = mock_of[MessageBus](
        subscribe=AsyncMock(spec=MessageBus.subscribe, return_value=None),
        unsubscribe=AsyncMock(spec=MessageBus.unsubscribe, return_value=None),
    )
    return cast("MessageBus", bus)


def _state_with_scheduler() -> AppState:
    """Build a state carrying both of the bridge's dependencies.

    Returns:
        An ``AppState`` with a message bus and a ceremony scheduler.
    """
    app_state = make_app_state(message_bus=_bus())
    app_state.wire(
        EngineStateSlice,
        ceremony_scheduler=MagicMock(spec=CeremonyScheduler),
    )
    return app_state


class TestWebhookBridgeWiring:
    async def test_builds_starts_and_wires_the_bridge(self) -> None:
        app_state = _state_with_scheduler()

        await wire_webhook_event_bridge(app_state)  # type: ignore[arg-type]

        bridge = app_state.slice(IntegrationsStateSlice).webhook_event_bridge  # type: ignore[attr-defined]
        assert isinstance(bridge, WebhookEventBridge)
        await bridge.stop()

    async def test_starting_before_wiring_leaves_a_failure_retryable(self) -> None:
        # A bridge committed to the slice before it started would read up
        # while subscribing to nothing, and the next pass would skip it.
        app_state = _state_with_scheduler()
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(
                WebhookEventBridge,
                "start",
                AsyncMock(
                    spec=WebhookEventBridge.start,
                    side_effect=RuntimeError("subscribe boom"),
                ),
            )
            with pytest.raises(RuntimeError, match="subscribe boom"):
                await wire_webhook_event_bridge(app_state)  # type: ignore[arg-type]

        assert (
            app_state.slice(IntegrationsStateSlice).webhook_event_bridge is None  # type: ignore[attr-defined]
        )

    async def test_declines_naming_the_absent_scheduler(self) -> None:
        app_state = make_app_state(message_bus=_bus())

        with pytest.raises(SubsystemDeclinedError, match="ceremony scheduler"):
            await wire_webhook_event_bridge(app_state)

    async def test_declines_naming_the_absent_bus(self) -> None:
        app_state = make_app_state()
        app_state.wire(
            EngineStateSlice,
            ceremony_scheduler=MagicMock(spec=CeremonyScheduler),
        )

        with pytest.raises(SubsystemDeclinedError, match="message bus"):
            await wire_webhook_event_bridge(app_state)

    async def test_a_second_pass_leaves_the_running_bridge_alone(self) -> None:
        app_state = _state_with_scheduler()
        await wire_webhook_event_bridge(app_state)  # type: ignore[arg-type]
        first = app_state.slice(IntegrationsStateSlice).webhook_event_bridge  # type: ignore[attr-defined]

        await wire_webhook_event_bridge(app_state)  # type: ignore[arg-type]

        assert app_state.slice(IntegrationsStateSlice).webhook_event_bridge is first  # type: ignore[attr-defined]
        assert first is not None
        await first.stop()
