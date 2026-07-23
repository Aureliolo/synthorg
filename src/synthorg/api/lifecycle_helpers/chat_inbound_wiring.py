# module-kind: code
"""On-startup wiring for the inbound Slack Socket-Mode consumer.

Builds the consumer from the boot-scoped thread registry (shared with the
notification sink), a router, and the approval-backed resume dispatcher,
wires the live settings resolver, and starts the resident loop. The
consumer is inert until an operator turns on ``tools.chat_inbound_enabled``
and points ``tools.chat_inbound_connection`` at a Slack connection holding
a Socket-Mode app token, so starting it unconditionally is safe. Returns
the consumer so the shutdown runner can stop it.
"""

from synthorg.api.chat_inbound_resume import ApprovalResumeDispatcher
from synthorg.api.state import AppState
from synthorg.integrations.chat_api.inbound.consumer import ChatInboundConsumer
from synthorg.integrations.chat_api.inbound.router import InboundResumeRouter
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP, API_SERVICE_AUTO_WIRED
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


async def start_chat_inbound_consumer(
    app_state: AppState,
) -> ChatInboundConsumer | None:
    """Build and start the inbound consumer (best-effort).

    Returns:
        The started consumer, or ``None`` when its collaborators (the
        connection catalog / thread registry) are not wired or the start
        failed (logged non-fatal).
    """
    integrations = app_state.slice(IntegrationsStateSlice)
    catalog = integrations.connection_catalog
    registry = integrations.inbound_thread_registry
    if catalog is None or registry is None:
        return None
    try:
        router = InboundResumeRouter(
            registry=registry,
            dispatcher=ApprovalResumeDispatcher(app_state),
        )
        consumer = ChatInboundConsumer(
            connection_catalog=catalog,
            router=router,
            config_resolver=config_resolver_of(app_state),
        )
        await consumer.start()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        from synthorg.core.critical_errors import reraise_critical  # noqa: PLC0415

        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            phase="chat_inbound_consumer_start",
            severity="non_fatal",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    logger.info(API_SERVICE_AUTO_WIRED, service="chat_inbound_consumer")
    return consumer


__all__ = ["start_chat_inbound_consumer"]
