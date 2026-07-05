"""Direct-MCP actor subscriber: rebuild the actor on a live toggle.

Watches ``chief_of_staff.direct_mcp_enabled`` and rebuilds the direct-MCP
conversational actor through the fail-closed builder whenever it changes, so
enabling or disabling ``POST /meta/chat/act`` takes effect with no restart.

The rebuild re-runs the same governance + MCP-self-consumer gate the startup
wirer uses (:func:`build_conversational_actor`), so a live enable materialises
the actor ONLY when the boot engine already carries security governance; a
toggle-on without governance stays fail-closed (the actor stays ``None`` and
the endpoint 503s). Because the governance re-check happens on every rebuild,
the flag no longer needs to be restart-bound: the historical restart
requirement existed solely because there was no live re-check path, and this
subscriber supplies one.

When the flag is enabled but the actor cannot build (no governance, no MCP
self-consumer, or no boot engine), the subscriber logs a concise cross-warning
naming the coupled prerequisite so the operator sees why acting stays inert.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {("chief_of_staff", "direct_mcp_enabled")},
)


class DirectMcpActorSettingsSubscriber:
    """Rebuild the direct-MCP actor when ``direct_mcp_enabled`` changes.

    Args:
        app_state: Application state holding the meta + runtime slices.
        settings_service: Settings service the config load reads from.
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
        """Human-readable subscriber name for logging."""
        return "direct-mcp-actor-settings"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Rebuild the fail-closed actor from the current config."""
        from synthorg.api.lifecycle_helpers.conversational_wiring import (  # noqa: PLC0415
            rebuild_conversational_actor,
        )
        from synthorg.meta.config import (  # noqa: PLC0415
            load_self_improvement_config,
        )
        from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

        try:
            si_config = await load_self_improvement_config(self._settings_service)
            await rebuild_conversational_actor(self._app_state, si_config=si_config)
            wired = (
                self._app_state.slice(MetaStateSlice).conversational_actor is not None
            )
            if si_config.chief_of_staff.direct_mcp_enabled and not wired:
                logger.warning(
                    SETTINGS_SUBSCRIBER_NOTIFIED,
                    subscriber=self.subscriber_name,
                    namespace=namespace,
                    key=key,
                    note=(
                        "direct MCP acting enabled but stays inert: no security"
                        " governance / MCP self-consumer / boot engine wired."
                        " Enable security.mcp_self_consumer + a SecurityConfig"
                        " to expose POST /meta/chat/act."
                    ),
                )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="conversational_actor",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            namespace=namespace,
            key=key,
            note="direct MCP actor rebuilt",
        )
