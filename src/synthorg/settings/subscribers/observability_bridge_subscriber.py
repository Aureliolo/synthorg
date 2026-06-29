"""Observability bridge-config settings subscriber.

Hot-swaps ``app_state.bridge_config.observability`` when an operator
edits a watched ``observability.*`` setting carried on
:class:`~synthorg.settings.bridge_configs.ObservabilityBridgeConfig`
(the HTTP log-handler batch knobs, the audit-chain signing timeout, and
the per-preset RFC 3161 TSA endpoints).

The snapshot is re-resolved wholesale (DB > env > default per field) and
swapped, mirroring :class:`MemoryBridgeSettingsSubscriber`. The four
``http_*`` batch knobs are additionally live-applied onto every installed
:class:`HttpBatchHandler` via ``apply_http_log_handler_settings`` (the same
helper the startup ``_apply_http_log_handler_config`` path uses), so an
operator edit takes effect on the next batch / POST without a restart. The
TSA-endpoint fields are baked into their handlers at ``configure_logging``
time, so a DB edit to those is ``restart_required`` -- this subscriber keeps
the snapshot authoritative for ``/settings`` reads in the meantime;
``audit_chain_signing_timeout_seconds`` is live-applied by its own path.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.bridge_configs import ObservabilityBridgeConfig
from synthorg.settings.service import SettingsService
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

_NAMESPACE = "observability"
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    (_NAMESPACE, k)
    for k in (
        "http_batch_size",
        "http_flush_interval_seconds",
        "http_timeout_seconds",
        "http_max_retries",
        "audit_chain_signing_timeout_seconds",
        "tsa_endpoint_freetsa",
        "tsa_endpoint_digicert",
        "tsa_endpoint_sectigo",
    )
)

# Surface a typo/rename in the watched set at import time, not on the
# next operator hot-reload (mirrors MemoryBridgeSettingsSubscriber).
_OBSERVABILITY_BRIDGE_FIELDS: frozenset[str] = frozenset(
    ObservabilityBridgeConfig.model_fields
)
for _, _key in _WATCHED:
    if _key not in _OBSERVABILITY_BRIDGE_FIELDS:
        msg = (
            f"ObservabilityBridgeSettingsSubscriber._WATCHED key {_key!r}"
            f" is not a field of ObservabilityBridgeConfig"
        )
        raise RuntimeError(msg)


class ObservabilityBridgeSettingsSubscriber:
    """Hot-swap the observability bridge config when watched settings change.

    On a watched-key change the full ``ObservabilityBridgeConfig`` is
    re-resolved via :meth:`ConfigResolver.get_observability_bridge_config`
    and applied through ``app_state.bridge_config.swap_observability``. A
    resolver/parse failure is logged and re-raised so the dispatcher
    records subscriber context; the prior snapshot stays because the swap
    never happens.

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
        return "observability-bridge-config"

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
        from synthorg.api.lifecycle_helpers.config_apply import (  # noqa: PLC0415
            apply_http_log_handler_settings,
        )

        try:
            resolver = config_resolver_of(self._app_state)
            snapshot = await resolver.get_observability_bridge_config()
            self._app_state.bridge_config.swap_observability(snapshot)
            apply_http_log_handler_settings(snapshot)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="observability_bridge_config",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
