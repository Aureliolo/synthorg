"""Escalation reconnect-delay settings subscriber.

Pushes ``communication.escalation_subscriber_reconnect_delay_seconds`` edits
onto the live Postgres LISTEN/NOTIFY escalation subscriber, whose reconnect
loop reads the back-off per attempt, so a change applies without a restart.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.communication.state import CommunicationStateSlice
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

_RECONNECT_KEY = "escalation_subscriber_reconnect_delay_seconds"
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {(SettingNamespace.COMMUNICATION.value, _RECONNECT_KEY)},
)


class EscalationReconnectSettingsSubscriber:
    """Apply escalation reconnect-delay changes onto the live subscriber.

    Args:
        app_state: Application state owning the escalation subscriber + resolver.
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
        return "escalation-reconnect"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Push each changed delay onto the escalation subscriber.

        Args:
            changes: The watched writes to apply.
        """
        for namespace, key in changes:
            await self._apply(namespace, key)

    async def _apply(self, namespace: str, key: str) -> None:
        """Resolve the new delay and push it onto the escalation subscriber."""
        if (namespace, key) not in _WATCHED:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected pair",
            )
            return
        subscriber = self._app_state.slice(
            CommunicationStateSlice
        ).escalation_notify_subscriber
        if subscriber is None:
            return
        try:
            value = await config_resolver_of(self._app_state).get_float(namespace, key)
            subscriber.set_reconnect_delay_seconds(value)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="escalation_notify_subscriber",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
