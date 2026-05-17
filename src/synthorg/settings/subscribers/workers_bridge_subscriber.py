"""Workers bridge-config settings subscriber.

Hot-swaps :attr:`AppState.workers_bridge_config` when an operator edits
a watched ``workers.*`` setting whose value lives on
:class:`~synthorg.settings.bridge_configs.WorkersBridgeConfig`. The
dispatcher already filters out ``restart_required=True`` keys before
invoking subscribers; the three dispatcher retry knobs are mutable
(Cat-1), so they hot-reload onto the next snapshot read.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.bridge_configs import WorkersBridgeConfig

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_NAMESPACE = "workers"
# Each watched key maps to whether it resolves as an int (vs float) so
# the resolver parses the operator value with the correct primitive
# before ``model_validate`` re-checks the Field bounds.
_WATCHED_INT: frozenset[str] = frozenset({"dispatcher_publish_max_attempts"})
_WATCHED_FLOAT: frozenset[str] = frozenset(
    {
        "dispatcher_publish_backoff_base_seconds",
        "dispatcher_publish_backoff_cap_seconds",
    }
)
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    (_NAMESPACE, k) for k in (_WATCHED_INT | _WATCHED_FLOAT)
)

# Surface a typo/rename in the watched set at import time, not on the
# next operator hot-reload (mirrors ApiBridgeSettingsSubscriber).
_WORKERS_BRIDGE_FIELDS: frozenset[str] = frozenset(WorkersBridgeConfig.model_fields)
for _, _key in _WATCHED:
    if _key not in _WORKERS_BRIDGE_FIELDS:
        msg = (
            f"WorkersBridgeSettingsSubscriber._WATCHED key {_key!r}"
            f" is not a field of WorkersBridgeConfig"
        )
        raise RuntimeError(msg)


class WorkersBridgeSettingsSubscriber:
    """Hot-swap ``workers_bridge_config`` when watched settings change.

    On a watched-key change the new value is resolved via
    :class:`~synthorg.settings.resolver.ConfigResolver` (int or float
    per the watched-set partition) and applied through
    ``AppState.mutate_workers_bridge_config({key: value})``, which
    re-validates the merged snapshot under the per-bridge lock so an
    out-of-range operator value raises and the prior snapshot is kept.

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
        return "workers-bridge-config"

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        """Resolve the new value and mutate the bridge-config snapshot."""
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
            resolver = self._app_state.config_resolver
            value: int | float
            if key in _WATCHED_INT:
                value = await resolver.get_int(namespace, key)
            else:
                value = await resolver.get_float(namespace, key)
            self._app_state.mutate_workers_bridge_config({key: value})
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="workers_bridge_config",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
