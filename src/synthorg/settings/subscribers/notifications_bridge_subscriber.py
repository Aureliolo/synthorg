"""Notifications bridge-config settings subscriber.

Rebuilds the live notification dispatcher (and its Slack / ntfy / email sinks)
with operator-tuned timeouts + default URLs when a watched ``notifications.*``
setting changes. Reuses ``config_apply._apply_notification_dispatcher_config``
(the same start-before-swap rebuild the startup path uses), so an operator edit
takes effect without a restart.
"""

from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_NAMESPACE = "notifications"
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    (_NAMESPACE, k)
    for k in (
        "slack_timeout_seconds",
        "ntfy_webhook_timeout_seconds",
        "email_smtp_timeout_seconds",
        "ntfy_default_url",
    )
)


class NotificationsBridgeSettingsSubscriber:
    """Rebuild the notification dispatcher on a watched ``notifications.*`` edit.

    Args:
        app_state: Application state owning the dispatcher + resolver.
        config: Effective root config (the dispatcher rebuild reads
            ``config.notifications`` for the sink topology).
        settings_service: Held for symmetry with peer subscribers.
    """

    def __init__(
        self,
        app_state: AppState,
        config: RootConfig,
        settings_service: SettingsService,
    ) -> None:
        self._app_state = app_state
        self._config = config
        self._settings_service = settings_service

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the ``(namespace, key)`` pairs this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logs."""
        return "notifications-bridge-config"

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        """Rebuild the dispatcher with the operator-tuned timeouts / URLs."""
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
            _apply_notification_dispatcher_config,
        )

        try:
            await _apply_notification_dispatcher_config(self._app_state, self._config)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="notifications_dispatcher",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
