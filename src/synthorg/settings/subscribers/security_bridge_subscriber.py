"""Security policy-toggle settings subscriber.

Rebuilds the live :class:`SecurityConfig` from the four operator-tunable
toggles (``security.enabled`` / ``audit_enabled`` / ``post_tool_scanning_enabled``
/ ``output_scan_policy_type``) and swaps it into ``app_state.security_runtime_config``,
which the per-request security interceptor reads -- so an enable / tighten (or a
guarded disable) applies on the next agent run without a restart.

The disable direction (true->false on a boolean, or ``output_scan_policy_type``
-> ``log_only``) is a security-weakening transition gated at the settings write
path by the deliberate confirm+reason guardrail; this subscriber is purely
mechanical and applies whatever value passed that gate.
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

_NAMESPACE = SettingNamespace.SECURITY.value
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    (_NAMESPACE, k)
    for k in (
        "enabled",
        "audit_enabled",
        "post_tool_scanning_enabled",
        "output_scan_policy_type",
    )
)


class SecurityBridgeSettingsSubscriber:
    """Rebuild + swap the live ``SecurityConfig`` on a watched security edit.

    Args:
        app_state: Application state owning the security holder + resolver.
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
        return "security-bridge-config"

    async def on_settings_changed(self, namespace: str, key: str) -> None:
        """Re-resolve the four toggles, rebuild the config, and swap it in."""
        if (namespace, key) not in _WATCHED:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected pair",
            )
            return
        base = self._app_state.config.security
        resolver = config_resolver_of(self._app_state)
        try:
            updates = {
                "enabled": await resolver.get_bool(_NAMESPACE, "enabled"),
                "audit_enabled": await resolver.get_bool(_NAMESPACE, "audit_enabled"),
                "post_tool_scanning_enabled": await resolver.get_bool(
                    _NAMESPACE, "post_tool_scanning_enabled"
                ),
                "output_scan_policy_type": await resolver.get_str(
                    _NAMESPACE, "output_scan_policy_type"
                ),
            }
            new_config = base.model_copy(update=updates)
            self._app_state.security_runtime_config.swap(new_config)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="security_runtime_config",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
