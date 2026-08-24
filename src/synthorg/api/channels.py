"""WebSocket channel constants, plugin factory, and shared publish helper.

Defines the named channels for real-time event feeds and
creates the Litestar ``ChannelsPlugin`` with an in-memory backend.
"""

from collections.abc import Callable
from typing import Final

from litestar import Request
from litestar.channels import ChannelsPlugin
from litestar.channels.backends.memory import MemoryChannelsBackend
from litestar.datastructures import State

from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_WS_SEND_FAILED

logger = get_logger(__name__)

#: Publishes ``plan.updated`` for one plan. Narrow on purpose: a plan writer
#: that outlives its request needs to announce a change, not the whole channels
#: surface, and holding the plugin itself would let it publish anything.
type PlanNotifier = Callable[[Plan], None]

CHANNEL_TASKS: Final[str] = "tasks"
CHANNEL_AGENTS: Final[str] = "agents"
CHANNEL_BUDGET: Final[str] = "budget"
CHANNEL_MESSAGES: Final[str] = "messages"
CHANNEL_SYSTEM: Final[str] = "system"
CHANNEL_APPROVALS: Final[str] = "approvals"
CHANNEL_ARTIFACTS: Final[str] = "artifacts"
CHANNEL_PROJECTS: Final[str] = "projects"
CHANNEL_PLANS: Final[str] = "plans"
CHANNEL_COMPANY: Final[str] = "company"
CHANNEL_DEPARTMENTS: Final[str] = "departments"
CHANNEL_CLIENTS: Final[str] = "clients"
CHANNEL_REQUESTS: Final[str] = "requests"
CHANNEL_SIMULATIONS: Final[str] = "simulations"
CHANNEL_REVIEWS: Final[str] = "reviews"
CHANNEL_EVENTS: Final[str] = "events"
CHANNEL_INTERRUPTS: Final[str] = "interrupts"
CHANNEL_COCKPIT: Final[str] = "cockpit"
CHANNEL_WORKFLOWS: Final[str] = "workflows"
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
    CHANNEL_ARTIFACTS,
    CHANNEL_PROJECTS,
    CHANNEL_PLANS,
    CHANNEL_COMPANY,
    CHANNEL_DEPARTMENTS,
    CHANNEL_CLIENTS,
    CHANNEL_REQUESTS,
    CHANNEL_SIMULATIONS,
    CHANNEL_REVIEWS,
    CHANNEL_EVENTS,
    CHANNEL_INTERRUPTS,
    CHANNEL_COCKPIT,
    CHANNEL_WORKFLOWS,
    CHANNEL_WEBHOOKS,
    CHANNEL_RATELIMIT,
)

# Channels whose events are sensitive and restricted to system roles
# (CEO/MANAGER): budget figures, and internal integration coordination
# channels that carry secrets or rate-limit signals.
# The dashboard SSE feed auto-subscribes every channel a caller may
# read, so an unrestricted sensitive channel would otherwise stream to
# any authenticated role; gating here closes that for both transports.
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


def publish_ws_event_with_plugin(
    channels_plugin: ChannelsPlugin | None,
    event_type: WsEventType,
    channel: str,
    payload: dict[str, object],
    *,
    clock: Clock,
) -> None:
    """Best-effort publish to a channel through an already-resolved plugin.

    The plugin-first form so a caller that outlives its request (a background
    task fired after the response returned) can still publish: it resolves the
    plugin while the request is live and hands it here. Logs and returns
    silently when the plugin is absent or the publish fails; ``MemoryError``
    and ``RecursionError`` always re-raise.

    Args:
        channels_plugin: The resolved plugin, or ``None`` to drop the event.
        event_type: Classification of the event.
        channel: Target channel name (shared channels from
            ``ALL_CHANNELS`` or dynamic ``user:{id}`` channels).
        payload: Event-specific data.
        clock: The application ``Clock`` seam, so the event timestamp honours a
            ``FakeClock`` under test rather than reading wall time directly.
    """
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
        timestamp=clock.now(),
        payload=payload,
    )
    try:
        channels_plugin.publish(
            event.model_dump_json(),
            channels=[channel],
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_WS_SEND_FAILED,
            event_type=event_type.value,
            channel=channel,
            note="Failed to publish WS event",
        )


def plan_updated_payload(
    plan: Plan, *, supersedes: Plan | None = None
) -> dict[str, object]:
    """The locator a ``plan.updated`` subscriber refetches from.

    One definition for every publisher, because the payload is a contract
    with the dashboard's handler and a background publisher that drifted from
    the controllers' shape would look identical on the wire until a
    subscriber read a key that was not there. Deliberately minimal: the event
    is a refresh signal, so a subscriber reloads rather than rendering this.

    ``supersedes`` is built here rather than merged in by the replan
    controller for that same reason: the key names a plan the subscriber
    refetches, so it is part of the locator, and a shape only one publisher
    knows about is the drift this function exists to prevent.

    Args:
        plan: The plan whose change is being announced.
        supersedes: The plan this one retires, when the change is a
            replan. A viewer sitting on the retired plan is not looking at
            ``plan``, so without this the successor's event names an id
            that viewer does not hold and its detail stays stale.

    Returns:
        The event payload.
    """
    payload: dict[str, object] = {
        "plan_id": str(plan.id),
        "version": plan.version,
        "status": plan.status.value,
    }
    if supersedes is not None:
        payload["supersedes"] = str(supersedes.id)
    return payload


def make_plan_notifier(
    channels_plugin: ChannelsPlugin, *, clock: Clock
) -> PlanNotifier:
    """Build the publisher a plan writer outside a request uses.

    The plan-review gate fills and parks a plan from a background spine, long
    after the request that started it returned, so it cannot resolve the plugin
    from a request the way a controller does. It is what keeps a page open
    during decomposition from rendering the pre-decomposition snapshot beside a
    fresh approval prompt.

    Args:
        channels_plugin: The plugin resolved once, at construction.
        clock: The application clock seam, so the event timestamp honours a
            ``FakeClock`` under test.

    Returns:
        A callable publishing ``plan.updated`` for one plan, over the shared
        :func:`plan_updated_payload`, so the dashboard's existing handler
        refetches with no change.
    """

    def _notify(plan: Plan) -> None:
        publish_ws_event_with_plugin(
            channels_plugin,
            WsEventType.PLAN_UPDATED,
            CHANNEL_PLANS,
            plan_updated_payload(plan),
            clock=clock,
        )

    return _notify


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
    app_state: AppState = request.app.state["app_state"]
    publish_ws_event_with_plugin(
        get_channels_plugin(request),
        event_type,
        channel,
        payload,
        clock=app_state.clock,
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
