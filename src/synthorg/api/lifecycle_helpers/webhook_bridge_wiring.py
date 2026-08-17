# module-kind: code
"""Startup wiring for the webhook-to-ceremony event bridge.

The bridge forwards verified inbound webhook deliveries into the active
sprint's external-trigger strategy, so it needs the ceremony scheduler
holding that sprint. It used to be built during construction, where the
scheduler is always absent, which left it unbuilt on every real boot: a
deployment with webhooks configured, verified and delivering forwarded
none of them into a ceremony.

The bridge starts here rather than in the startup runner because the
runner reads the slice once, at boot, and this subsystem activates on a
later pass; a bridge nobody started is a subscription that never happens.
Stopping stays with the runner's shutdown, which reads the slice live.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.communication.state import CommunicationStateSlice
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.workflow.webhook_bridge import WebhookEventBridge
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


async def wire_webhook_event_bridge(app_state: AppState) -> None:
    """Build, start and wire the webhook event bridge.

    Args:
        app_state: The application state holding the collaborator slices.

    Raises:
        SubsystemDeclinedError: A collaborator the bridge is built from is
            not wired yet, naming which.
    """
    if app_state.slice(IntegrationsStateSlice).webhook_event_bridge is not None:
        return

    message_bus = app_state.slice(CommunicationStateSlice).message_bus
    ceremony_scheduler = app_state.slice(EngineStateSlice).ceremony_scheduler
    missing = [
        name
        for name, present in (
            ("message bus", message_bus is not None),
            ("ceremony scheduler", ceremony_scheduler is not None),
        )
        if not present
    ]
    if message_bus is None or ceremony_scheduler is None:
        msg = f"waiting on: {', '.join(missing)}"
        raise SubsystemDeclinedError(msg)

    bridge = WebhookEventBridge(
        bus=message_bus,
        ceremony_scheduler=ceremony_scheduler,
        config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
        clock=app_state.clock,
    )
    await bridge.start()
    app_state.wire(IntegrationsStateSlice, webhook_event_bridge=bridge)
    logger.info(API_APP_STARTUP, service="webhook_event_bridge", note="wired")


__all__ = ["wire_webhook_event_bridge"]
