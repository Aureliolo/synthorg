"""Meta self-improvement settings subscriber.

Invalidates the cached :class:`~synthorg.meta.config.SelfImprovementConfig`
on the meta slice when an operator edits ``meta.self_improvement``. The
meta slice caches the parsed config so the read endpoints do not re-parse
the JSON per request; this subscriber wires the cache field back to
``None`` so the next read reloads the fresh value, preserving hot-reload.
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

_WATCHED: frozenset[tuple[str, str]] = frozenset({("meta", "self_improvement")})


class MetaSelfImprovementSettingsSubscriber:
    """Invalidate the cached ``SelfImprovementConfig`` on a config edit.

    Holds :class:`AppState` (where the cache lives) and
    :class:`SettingsService` (for parity with peer subscribers). On a
    watched-key change it wires ``MetaStateSlice.self_improvement_config``
    back to ``None`` so the next :func:`self_improvement_config_of` read
    reloads the operator's new value.

    Args:
        app_state: Application state that owns the cached config.
        settings_service: Settings service held for symmetry with peers.
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
        return "meta-self-improvement"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Invalidate the cached config so the next read reloads it."""
        from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

        try:
            self._app_state.wire(MetaStateSlice, self_improvement_config=None)
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="invalidated cached self-improvement config",
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="meta_self_improvement",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
