"""Memory bridge-config settings subscriber.

Hot-swaps ``app_state.bridge_config.memory`` when an operator edits
a watched ``memory.*`` setting whose value lives on
:class:`~synthorg.settings.bridge_configs.MemoryBridgeConfig` (the
consolidation enforce-batch size and the embedding fine-tune preflight
VRAM table + chunk size).

Unlike the api/workers bridges (single scalar per key), this bridge
carries a structured JSON field (``fine_tune_vram_batch_table``) whose
parse + ordering validation lives in
:meth:`ConfigResolver.get_memory_bridge_config`. So on any watched-key
change the subscriber re-resolves the whole snapshot through that
method and swaps it wholesale -- each field is still resolved
independently (DB > env > default), and an invalid operator value
raises there so the prior snapshot is retained.

The separate notify-only ``MemorySettingsSubscriber`` continues to
cover the non-bridge ``memory.default_level`` /
``memory.consolidation_interval`` keys; this subscriber owns only the
three bridge-backed keys.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.bridge_configs import MemoryBridgeConfig
from synthorg.settings.service import SettingsService
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

_NAMESPACE = "memory"
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    (_NAMESPACE, k)
    for k in (
        "consolidation_enforce_batch_size",
        "fine_tune_vram_batch_table",
        "fine_tune_chunk_size",
    )
)

# Surface a typo/rename in the watched set at import time, not on the
# next operator hot-reload (mirrors ApiBridgeSettingsSubscriber).
_MEMORY_BRIDGE_FIELDS: frozenset[str] = frozenset(MemoryBridgeConfig.model_fields)
for _, _key in _WATCHED:
    if _key not in _MEMORY_BRIDGE_FIELDS:
        msg = (
            f"MemoryBridgeSettingsSubscriber._WATCHED key {_key!r}"
            f" is not a field of MemoryBridgeConfig"
        )
        raise RuntimeError(msg)


class MemoryBridgeSettingsSubscriber:
    """Hot-swap the memory bridge config when watched settings change.

    On a watched-key change the full ``MemoryBridgeConfig`` is
    re-resolved via :meth:`ConfigResolver.get_memory_bridge_config`
    (which parses + ordering-validates the JSON VRAM table) and applied
    through ``app_state.bridge_config.swap_memory``. A resolver/parse
    failure is logged and re-raised so the dispatcher records
    subscriber context; the prior snapshot stays because the swap never
    happens.

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
        return "memory-bridge-config"

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        """Re-resolve the whole snapshot and swap it atomically."""
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
            resolver = config_resolver_of(self._app_state)
            snapshot = await resolver.get_memory_bridge_config()
            self._app_state.bridge_config.swap_memory(snapshot)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="memory_bridge_config",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
