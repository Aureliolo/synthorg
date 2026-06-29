# module-kind: code
"""Live capability gate for ghost-wired, on-by-default features.

A feature whose service is built at startup (ghost-wired) is gated here per
request: each call reads the feature's ``<namespace>.<key>`` flag fresh from the
settings resolver, so toggling it in dashboard Settings takes effect with no
restart. When the flag is off the entrypoint 503s with a clear, settings-pointing
message rather than serving a capability the operator has turned off. Used by the
Chief-of-Staff chat capabilities, the research and knowledge MCP tools, and the
auto-scaling evaluate endpoint.
"""

from synthorg.api.state import AppState
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_FEATURE_GATE_BLOCKED
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


async def ensure_feature_enabled(
    app_state: AppState,
    namespace: str,
    key: str,
    *,
    feature_label: str,
) -> None:
    """Raise ``ServiceUnavailableError`` when a capability flag is off.

    Reads the flag live (DB > env > default) so a Settings toggle takes
    effect on the next request with no restart.

    Args:
        app_state: The application state (carries the config resolver).
        namespace: The setting namespace (e.g. ``"chief_of_staff"``).
        key: The boolean setting key (e.g. ``"group_chat_enabled"``).
        feature_label: Human-readable capability name for the 503 message.

    Raises:
        ServiceUnavailableError: When the flag resolves to ``False``.
    """
    if await config_resolver_of(app_state).get_bool(namespace, key):
        return
    logger.warning(
        API_FEATURE_GATE_BLOCKED,
        namespace=namespace,
        key=key,
        feature_label=feature_label,
        hint=f"{feature_label} is disabled; enable {namespace}.{key} in settings.",
    )
    msg = (
        f"{feature_label} is disabled. Enable ``{namespace}.{key}`` in"
        " dashboard Settings."
    )
    raise ServiceUnavailableError(msg)
