"""Telemetry opt-in settings subscriber.

Pushes ``telemetry.enabled`` onto the live collector. The collector stays
resident either way and short-circuits when disabled, so the switch is a
question of whether anything is sent, not of whether the machinery exists.
"""

from collections.abc import Sequence

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
from synthorg.telemetry.state import TelemetryStateSlice

logger = get_logger(__name__)

_NAMESPACE = SettingNamespace.TELEMETRY.value
_KEY = "enabled"
_WATCHED: frozenset[tuple[str, str]] = frozenset({(_NAMESPACE, _KEY)})


class TelemetrySettingsSubscriber:
    """Apply the telemetry opt-in to the live collector.

    Args:
        app_state: Application state owning the collector and the resolver.
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
        return "telemetry-opt-in"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Apply each changed opt-in to the collector.

        Args:
            changes: The watched writes to apply.
        """
        for namespace, key in changes:
            await self._apply_change(namespace, key)

    async def _apply_change(self, namespace: str, key: str) -> None:
        """Resolve the opt-in and apply it to the collector.

        Raises:
            Exception: Re-raised after logging so the dispatcher records the
                failure with subscriber context. The previous state stays in
                force, which for an opt-out means it stays opted out.
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
        collector = self._app_state.slice(TelemetryStateSlice).collector
        if collector is None:
            return
        try:
            enabled = await config_resolver_of(self._app_state).get_bool(namespace, key)
            collector.apply_resolved_enabled(enabled=enabled)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="telemetry_collector",
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
            note="telemetry opt-in applied to the live collector",
        )
