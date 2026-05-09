"""API bridge-config settings subscriber.

Hot-swaps :attr:`AppState.api_bridge_config` when an operator edits a
watched ``api.*`` setting whose value lives on
:class:`~synthorg.settings.bridge_configs.ApiBridgeConfig`. The
dispatcher already filters out ``restart_required=True`` keys before
invoking subscribers, so the watched set here only enumerates the
hot-reloadable fields.

Watches ``api.max_lifecycle_events_per_query``, consumed by
:class:`~synthorg.api.controllers.activities.ActivityController` as
its ``LIMIT`` clamp on the lifecycle-events query. Additional
``ApiBridgeConfig`` fields can be appended to ``_WATCHED`` when their
consumers need hot-reload semantics.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.bridge_configs import ApiBridgeConfig

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

# Surface a typo or rename in ``_WATCHED`` at import time rather than at
# the next operator hot-reload. ``model_copy(update={key: value})`` would
# raise ValidationError because ``ApiBridgeConfig`` has ``extra="forbid"``,
# but only when the subscriber actually fires, so a deployment can ship
# with a broken watch list and never notice until a customer edits the
# offending setting.
_API_BRIDGE_FIELDS: frozenset[str] = frozenset(ApiBridgeConfig.model_fields)
for _ns, _key in _WATCHED:
    if _key not in _API_BRIDGE_FIELDS:
        msg = (
            f"ApiBridgeSettingsSubscriber._WATCHED key {_key!r}"
            f" is not a field of ApiBridgeConfig"
        )
        raise RuntimeError(msg)


class ApiBridgeSettingsSubscriber:
    """Hot-swap ``api_bridge_config`` when watched API settings change.

    Holds references to :class:`AppState` (where the snapshot lives)
    and :class:`SettingsService` (carried for parity with peer
    subscribers; the resolver is reached via ``app_state.config_resolver``
    so the subscriber sees the same DB > env > YAML > default chain
    every other consumer does).

    On a watched-key change the subscriber resolves the integer value
    via :class:`~synthorg.settings.resolver.ConfigResolver` and applies
    it through ``AppState.mutate_api_bridge_config({key: value})``,
    which merges the single-field update into the current snapshot
    under a per-bridge lock and re-validates via ``model_validate``
    (Pydantic v2 skips validators on the bare ``model_copy(update=...)``
    path). Resolver failures and validation errors are logged via
    ``SETTINGS_SERVICE_SWAP_FAILED`` and re-raised so the dispatcher
    records subscriber context; the previous snapshot stays in place.

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
            self._app_state.mutate_api_bridge_config({key: value})
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
