"""Tools bridge-config settings subscriber.

Hot-swaps ``app_state.bridge_config.tools`` and re-seeds the process-singleton
sidecar-resolution cache when an operator edits a watched ``tools.*`` Docker
sidecar / stop-grace setting. The Docker sandbox sidecar + lifecycle code reads
the cache per container launch / stop, so a re-seed takes effect on the next
container without a restart.

The full ``ToolsBridgeConfig`` is re-resolved wholesale (DB > env > default per
field) and swapped, mirroring :class:`ObservabilityBridgeSettingsSubscriber`.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
)
from synthorg.settings.bridge_configs import ToolsBridgeConfig
from synthorg.settings.service import SettingsService
from synthorg.settings.state import config_resolver_of
from synthorg.settings.subscriber import describe_changes
from synthorg.tools.sandbox._sidecar_resolution import set_resolved_sidecar_limits

logger = get_logger(__name__)

_NAMESPACE = "tools"
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    (_NAMESPACE, k)
    for k in (
        "docker_sidecar_health_poll_interval_seconds",
        "docker_sidecar_health_timeout_seconds",
        "docker_sidecar_memory_limit",
        "docker_sidecar_cpu_limit",
        "docker_sidecar_max_pids",
        "docker_stop_grace_timeout_seconds",
    )
)

# Surface a typo/rename in the watched set at import time, not on the
# next operator hot-reload (mirrors the peer bridge subscribers).
_TOOLS_BRIDGE_FIELDS: frozenset[str] = frozenset(ToolsBridgeConfig.model_fields)
for _, _key in _WATCHED:
    if _key not in _TOOLS_BRIDGE_FIELDS:
        msg = (
            f"ToolsBridgeSettingsSubscriber._WATCHED key {_key!r}"
            f" is not a field of ToolsBridgeConfig"
        )
        raise RuntimeError(msg)


class ToolsBridgeSettingsSubscriber:
    """Hot-swap the tools bridge config + sidecar cache on a watched change.

    On a watched-key change the full ``ToolsBridgeConfig`` is re-resolved via
    :meth:`ConfigResolver.get_tools_bridge_config`, swapped onto
    ``app_state.bridge_config``, and pushed into the sidecar-resolution cache.
    A resolver/parse failure is logged and re-raised so the dispatcher records
    subscriber context; the prior snapshot + cache stay because neither the
    swap nor the cache write happens.

    Args:
        app_state: Application state that owns the live snapshot.
        settings_service: Held for symmetry with peer subscribers.
    """

    def __init__(
        self,
        app_state: AppState,
        settings_service: SettingsService,
    ) -> None:
        self._app_state = app_state
        self._settings_service = settings_service

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the ``(namespace, key)`` pairs this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logs."""
        return "tools-bridge-config"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Re-resolve the whole snapshot, swap it, and re-seed the cache.

        One swap per batch: the snapshot is re-resolved from every key it
        covers, so re-running it once per changed key would repeat identical
        work and publish the same snapshot several times.

        Args:
            changes: The watched writes this swap carries.
        """
        try:
            resolver = config_resolver_of(self._app_state)
            snapshot = await resolver.get_tools_bridge_config()
            self._app_state.bridge_config.swap_tools(snapshot)
            set_resolved_sidecar_limits(snapshot)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="tools_bridge_config",
                trigger=describe_changes(changes),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
