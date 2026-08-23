# module-kind: code
"""Live capability gate for ghost-wired, on-by-default features.

A feature whose service is built at startup (ghost-wired) is gated here per
request: each call reads the feature's ``<namespace>.<key>`` flag fresh from the
settings resolver, so toggling it in dashboard Settings takes effect with no
restart. When the flag is off the entrypoint 503s with a clear, settings-pointing
message rather than serving a capability the operator has turned off. Used by the
Chief-of-Staff chat capabilities and the research and knowledge MCP tools.

This lives in the settings layer (not ``api``) so the ``meta`` MCP handlers gate
through ``synthorg.settings`` (which they already depend on for the config
resolver) instead of importing ``api`` directly. The shared ``AppStateSliceMixin``
annotation is sourced from ``api.state_slices`` at runtime, the same accepted
``settings -> api`` edge ``settings.state``'s accessors already carry (the
``.importlinter`` contracts permit it; a ``TYPE_CHECKING``-only import would break
the ``--typeguard-forward-ref-policy=ERROR`` runtime check). The gate's own
resolver lookup is deferred to request time.
"""

from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_FEATURE_GATE_BLOCKED

logger = get_logger(__name__)


async def ensure_feature_enabled(
    app_state: AppStateSliceMixin,
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
    # Deferred: ``settings.state`` pulls ``infrastructure.services`` ->
    # ``communication`` into the cold-import graph, so a module-level import
    # here would surface that package's latent init cycle whenever a lean
    # consumer (api/_feature_gate) imports this gate before communication is
    # warm. The resolver lookup only runs at request time, by which point the
    # graph is fully initialised.
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

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
