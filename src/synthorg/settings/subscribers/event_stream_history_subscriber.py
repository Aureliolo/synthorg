"""Event-stream history settings subscriber.

Pushes operator edits to the SSE replay history bounds onto the live
``EventStreamHub`` (``communication.event_stream_history_max_sessions`` /
``event_stream_history_per_session``). The janitor interval + idle TTL are
re-read by the janitor itself each sweep, so they need no subscriber; the
history bounds are instance fields on the hub's ledger and are pushed in here.
"""

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

_NAMESPACE = SettingNamespace.COMMUNICATION.value
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        (_NAMESPACE, "event_stream_history_max_sessions"),
        (_NAMESPACE, "event_stream_history_per_session"),
        (_NAMESPACE, "event_stream_max_queue_size"),
    }
)


class EventStreamHistorySettingsSubscriber:
    """Apply SSE replay history-bound changes onto the live hub.

    Args:
        app_state: Application state owning the event-stream hub + resolver.
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
        return "event-stream-history"

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        """Resolve the new bound and push it onto the hub's ledger."""
        if (namespace, key) not in _WATCHED:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected pair",
            )
            return
        hub = self._app_state.slice(CommunicationStateSlice).event_stream_hub
        if hub is None:
            return
        try:
            value = await config_resolver_of(self._app_state).get_int(namespace, key)
            if key == "event_stream_history_max_sessions":
                hub.set_history_max_sessions(value)
            elif key == "event_stream_max_queue_size":
                hub.set_max_queue_size(value)
            else:
                hub.set_history_per_session(value)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="event_stream_hub",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
