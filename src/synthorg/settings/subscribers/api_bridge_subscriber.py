"""API bridge-config settings subscriber.

Hot-swaps :attr:`AppState.api_bridge_config` when an operator edits a
watched ``api.*`` setting whose value lives on
:class:`~synthorg.settings.bridge_configs.ApiBridgeConfig`. The
dispatcher already filters out ``restart_required=True`` keys before
invoking subscribers, so the watched set here only enumerates the
hot-reloadable fields.

Currently watches ``api.max_lifecycle_events_per_query``, consumed by
:class:`~synthorg.api.controllers.activities.ActivityController` as
its ``LIMIT`` clamp on the lifecycle-events query. Other
``ApiBridgeConfig`` fields can be added to ``_WATCHED`` as their
controllers migrate off per-request fallback constants.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_NAMESPACE = "api"
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        (_NAMESPACE, "max_lifecycle_events_per_query"),
    }
)


class ApiBridgeSettingsSubscriber:
    """Hot-swap ``api_bridge_config`` when watched API settings change.

    Holds references to :class:`AppState` (where the snapshot lives)
    and :class:`SettingsService` (carried for parity with peer
    subscribers; the resolver is reached via ``app_state.config_resolver``
    so the subscriber sees the same DB > env > YAML > default chain
    every other consumer does).

    On a watched-key change the subscriber resolves the integer value
    via :class:`~synthorg.settings.resolver.ConfigResolver`, builds the
    next snapshot through ``model_copy(update=...)`` (preserves every
    other field), and hands it to ``AppState.swap_api_bridge_config``.
    Resolver failures are logged via ``SETTINGS_SERVICE_SWAP_FAILED``
    and re-raised so the dispatcher records subscriber context; the
    previous snapshot stays in place.

    Args:
        app_state: Application state that owns the live snapshot.
        settings_service: Settings service held for symmetry with peer
            subscribers.
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
        return "api-bridge-config"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Resolve the new value and swap the bridge-config snapshot."""
        if (namespace, key) not in _WATCHED:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected pair",
            )
            return
        try:
            value = await self._app_state.config_resolver.get_int(namespace, key)
            new_config = self._app_state.api_bridge_config.model_copy(
                update={key: value},
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="api_bridge_config",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        self._app_state.swap_api_bridge_config(new_config)
