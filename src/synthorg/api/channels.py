"""WebSocket channel constants, plugin factory, and shared publish helper.

Defines the named channels for real-time event feeds and
creates the Litestar ``ChannelsPlugin`` with an in-memory backend.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from litestar.channels import ChannelsPlugin
from litestar.channels.backends.memory import MemoryChannelsBackend
from litestar.datastructures import State

from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_WS_SEND_FAILED

if TYPE_CHECKING:
    from litestar import Request

logger = get_logger(__name__)

CHANNEL_TASKS: Final[str] = "tasks"
CHANNEL_AGENTS: Final[str] = "agents"
CHANNEL_BUDGET: Final[str] = "budget"
CHANNEL_MESSAGES: Final[str] = "messages"
CHANNEL_SYSTEM: Final[str] = "system"
CHANNEL_APPROVALS: Final[str] = "approvals"
CHANNEL_MEETINGS: Final[str] = "meetings"
CHANNEL_ARTIFACTS: Final[str] = "artifacts"
CHANNEL_PROJECTS: Final[str] = "projects"
CHANNEL_COMPANY: Final[str] = "company"
CHANNEL_DEPARTMENTS: Final[str] = "departments"
CHANNEL_CLIENTS: Final[str] = "clients"
CHANNEL_REQUESTS: Final[str] = "requests"
CHANNEL_SIMULATIONS: Final[str] = "simulations"
CHANNEL_REVIEWS: Final[str] = "reviews"
CHANNEL_EVENTS: Final[str] = "events"
CHANNEL_INTERRUPTS: Final[str] = "interrupts"
CHANNEL_COCKPIT: Final[str] = "cockpit"
CHANNEL_DISSENT: Final[str] = "#dissent"
CHANNEL_WEBHOOKS: Final[str] = "#webhooks"
CHANNEL_RATELIMIT: Final[str] = "#ratelimit"

CHANNEL_USER_PREFIX: Final[str] = "user:"

ALL_CHANNELS: Final[tuple[str, ...]] = (
    CHANNEL_TASKS,
    CHANNEL_AGENTS,
    CHANNEL_BUDGET,
    CHANNEL_MESSAGES,
    CHANNEL_SYSTEM,
    CHANNEL_APPROVALS,
    CHANNEL_MEETINGS,
    CHANNEL_ARTIFACTS,
    CHANNEL_PROJECTS,
    CHANNEL_COMPANY,
    CHANNEL_DEPARTMENTS,
    CHANNEL_CLIENTS,
    CHANNEL_REQUESTS,
    CHANNEL_SIMULATIONS,
    CHANNEL_REVIEWS,
    CHANNEL_EVENTS,
    CHANNEL_INTERRUPTS,
    CHANNEL_COCKPIT,
    CHANNEL_DISSENT,
    CHANNEL_WEBHOOKS,
    CHANNEL_RATELIMIT,
)

# Channels whose events are sensitive and restricted to system roles
# (CEO/MANAGER). Includes budget channels and internal integration
# coordination channels that carry secrets or rate-limit signals.
BUDGET_CHANNELS: Final[frozenset[str]] = frozenset(
    {CHANNEL_BUDGET, CHANNEL_WEBHOOKS, CHANNEL_RATELIMIT}
)


def user_channel(user_id: str) -> str:
    """Return the user-scoped channel name.

    Args:
        user_id: The user's unique identifier.

    Returns:
        Channel name like ``user:abc123``.
    """
    return f"{CHANNEL_USER_PREFIX}{user_id}"


def is_user_channel(channel: str) -> bool:
    """Check whether a channel name is a user-scoped channel.

    Args:
        channel: Channel name to check.

    Returns:
        ``True`` if the channel starts with ``user:``.
    """
    return channel.startswith(CHANNEL_USER_PREFIX)


def extract_user_id(channel: str) -> str | None:
    """Extract the user ID from a user-scoped channel name.

    Args:
        channel: Channel name like ``user:abc123``.

    Returns:
        The user ID, or ``None`` if not a user channel.
    """
    if not channel.startswith(CHANNEL_USER_PREFIX):
        return None
    return channel[len(CHANNEL_USER_PREFIX) :]


def get_channels_plugin(
    request: Request[object, object, State],
) -> ChannelsPlugin | None:
    """Extract the ``ChannelsPlugin`` from the application, or ``None``.

    Args:
        request: The incoming Litestar request.

    Returns:
        The ``ChannelsPlugin`` instance if registered, otherwise ``None``.
    """
    for plugin in request.app.plugins:
        if isinstance(plugin, ChannelsPlugin):
            return plugin
    return None


def publish_ws_event(
    request: Request[object, object, State],
    event_type: WsEventType,
    channel: str,
    payload: dict[str, object],
) -> None:
    """Best-effort publish an event to a named WebSocket channel.

    Logs a warning and returns silently if the ``ChannelsPlugin``
    is not registered or the publish call fails.  ``MemoryError``
    and ``RecursionError`` are always re-raised.

    Args:
        request: The incoming Litestar request.
        event_type: Classification of the event.
        channel: Target channel name (shared channels from
            ``ALL_CHANNELS`` or dynamic ``user:{id}`` channels).
        payload: Event-specific data.
    """
    channels_plugin = get_channels_plugin(request)
    if channels_plugin is None:
        logger.warning(
            API_WS_SEND_FAILED,
            note="ChannelsPlugin not available, dropping WS event",
            event_type=event_type.value,
            channel=channel,
        )
        return

    event = WsEvent(
        event_type=event_type,
        channel=channel,
        timestamp=datetime.now(UTC),
        payload=payload,
    )
    try:
        channels_plugin.publish(
            event.model_dump_json(),
            channels=[channel],
        )
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_WS_SEND_FAILED,
            event_type=event_type.value,
            channel=channel,
            note="Failed to publish WS event",
        )


def create_channels_plugin() -> ChannelsPlugin:
    """Create the channels plugin with in-memory backend.

    Arbitrary channels are enabled for dynamic ``user:{id}``
    channels.  Server-side access control in the WS handler
    restricts which channels each user can subscribe to.

    Returns:
        Configured ``ChannelsPlugin`` with 20-message history.
    """
    return ChannelsPlugin(
        backend=MemoryChannelsBackend(history=20),
        channels=ALL_CHANNELS,
        arbitrary_channels_allowed=True,
    )
