"""Output-style policy settings subscriber.

Rebuilds the :class:`OutputStylePolicyService` and re-binds the ambient service
and house-style provider when an operator edits any ``output_style`` setting, so
a pack swap, an enable/shadow toggle, or an exemption change takes effect on the
next output boundary and prompt build with no restart.
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

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("output_style", "enabled"),
        ("output_style", "shadow_mode"),
        ("output_style", "pack"),
        ("output_style", "house_style_enabled"),
        ("output_style", "exemptions"),
    }
)


class OutputStyleSettingsSubscriber:
    """Rebuild + re-bind the output-style policy on a watched edit.

    Args:
        app_state: Held for symmetry with peer subscribers (unused: the ambient
            service and provider are process-global, not app-state slices).
        settings_service: The live resolver the rebuild reads the new config from.
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
        return "output-style"

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        """Rebuild the policy service so the new value goes live."""
        if (namespace, key) not in _WATCHED:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected pair",
            )
            return
        from synthorg.engine.output_style.wiring import (  # noqa: PLC0415
            rebuild_and_bind_output_style,
        )

        try:
            # rebuild_and_bind_output_style emits OUTPUT_STYLE_SERVICE_REBUILT
            # once the new service is bound; no second emission here.
            await rebuild_and_bind_output_style(self._settings_service)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="output_style_policy",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
