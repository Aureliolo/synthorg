"""Engine timeout-enforcement settings subscriber.

Pushes ``engine.timeout_enforcement_enabled`` edits into the process cache that
``engine.timeout_enforcement.engine_timeout`` reads per coroutine entry, so the
mutable kill-switch flips without a restart. The startup application of the same
setting lives in ``config_apply._apply_timeout_enforcement``; this subscriber is
the hot-reload counterpart, and shares its fail-safe-to-enabled discipline (a
resolver outage must never silently disable timeout enforcement).
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

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {("engine", "timeout_enforcement_enabled")},
)


class EngineTimeoutEnforcementSettingsSubscriber:
    """Apply ``engine.timeout_enforcement_enabled`` to the process cache.

    Args:
        app_state: Application state carrying the config resolver.
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
        return "engine-timeout-enforcement"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Push each changed flag into the timeout-enforcement cache.

        Args:
            changes: The watched writes to apply.
        """
        for namespace, key in changes:
            await self._apply_change(namespace, key)

    async def _apply_change(self, namespace: str, key: str) -> None:
        """Resolve the flag and push it into the timeout-enforcement cache."""
        if (namespace, key) not in _WATCHED:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected pair",
            )
            return
        from synthorg.engine.timeout_enforcement import (  # noqa: PLC0415
            set_timeout_enforcement_enabled,
        )

        try:
            value = await config_resolver_of(self._app_state).get_bool(
                SettingNamespace.ENGINE.value,
                "timeout_enforcement_enabled",
            )
        except Exception as exc:
            reraise_critical(exc)
            # Fail-safe to enabled: a settings-backend hiccup must never
            # silently turn timeout enforcement off (mirrors the startup
            # applier). Force the cache back on and surface the failure.
            set_timeout_enforcement_enabled(value=True)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="engine_timeout_enforcement",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                fallback_enabled=True,
            )
            raise
        set_timeout_enforcement_enabled(value=value)
