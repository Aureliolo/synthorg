"""A2A client-timeout settings subscriber.

Pushes ``a2a.client_timeout_seconds`` edits onto the live A2A federation
client, whose ``_do_post`` passes the timeout per request, so a change applies
without a restart.
"""

from collections.abc import Sequence

from synthorg.a2a.state import A2aStateSlice
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

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {(SettingNamespace.A2A.value, "client_timeout_seconds")},
)


class A2AClientSettingsSubscriber:
    """Apply ``a2a.client_timeout_seconds`` onto the live A2A client.

    Args:
        app_state: Application state owning the A2A slice + resolver.
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
        return "a2a-client"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Apply each changed timeout onto the live A2A client.

        Args:
            changes: The watched writes to apply.
        """
        for namespace, key in changes:
            await self._apply(namespace, key)

    async def _apply(self, namespace: str, key: str) -> None:
        """Resolve the new timeout and push it onto the A2A client."""
        if (namespace, key) not in _WATCHED:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected pair",
            )
            return
        client = self._app_state.slice(A2aStateSlice).client
        if client is None:
            return
        try:
            value = await config_resolver_of(self._app_state).get_float(namespace, key)
            client.set_timeout_seconds(value)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="a2a_client",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
