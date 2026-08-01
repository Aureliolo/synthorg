"""Response-compression threshold settings subscriber.

Pushes ``api.compression_minimum_size_bytes`` onto the ``CompressionConfig``
object Litestar was built with, via ``app_state.compression``. Litestar's
compression middleware reads ``config.minimum_size`` per response off that
same object, so mutating it is what the threshold takes to change without
rebuilding the app.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.service import SettingsService
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

_NAMESPACE = SettingNamespace.API.value
_KEY = "compression_minimum_size_bytes"
_WATCHED: frozenset[tuple[str, str]] = frozenset({(_NAMESPACE, _KEY)})


class CompressionSettingsSubscriber:
    """Apply the compression threshold to the live Litestar config.

    Args:
        app_state: Application state owning the config and the resolver.
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
        return "compression-threshold"

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        """Resolve the new threshold and apply it to the live config.

        Raises:
            ValueError: When the resolved threshold is not positive.
            Exception: Re-raised after logging so the dispatcher records the
                failure with subscriber context. The previous threshold stays
                in force.
        """
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
            value = await config_resolver_of(self._app_state).get_int(namespace, key)
            # ``set_minimum_size`` re-enforces the positive floor Litestar's own
            # constructor applies, so a bad edit cannot leave the middleware in
            # a state ``CompressionConfig`` would have refused to be built in.
            applied = self._app_state.compression.set_minimum_size(value)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="compression_config",
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
            note=(
                "compression threshold applied to the live config"
                if applied
                else "no app built yet; nothing to retune"
            ),
        )
