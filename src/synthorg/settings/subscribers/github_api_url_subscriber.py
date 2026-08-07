"""GitHub API URL settings subscriber: re-binds the health-checker base URL.

``integrations.github_api_url`` is resolved at startup and injected into the
import-time GitHub health checker via ``bind_github_default_api_url`` so GitHub
Enterprise health probes target the operator endpoint. This subscriber
re-resolves the value from the live settings DB and re-binds it on a change, so
the probe target updates without a restart. (The code-modification GitHub
client also reads this URL, but it is built into the restart-bound
self-improvement code-mod subsystem, so that consumer applies a change on the
next restart.)
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import SETTINGS_SERVICE_SWAP_FAILED
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.service import SettingsService
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset({("integrations", "github_api_url")})


class GithubApiUrlSettingsSubscriber:
    """Re-bind the GitHub health-checker base URL on a ``github_api_url`` change.

    Args:
        app_state: Application state holding the config resolver.
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
        """Return the ``integrations.github_api_url`` key this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logging."""
        return "github-api-url"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Re-bind the health checker for each changed key.

        Args:
            changes: The watched writes to apply.
        """
        for namespace, key in changes:
            await self._apply(namespace, key)

    async def _apply(self, namespace: str, key: str) -> None:
        """Re-resolve ``github_api_url`` and re-bind it onto the health checker."""
        from synthorg.integrations.health.prober import (  # noqa: PLC0415
            bind_github_default_api_url,
        )

        try:
            api_url = await config_resolver_of(self._app_state).get_str(
                SettingNamespace.INTEGRATIONS, "github_api_url"
            )
            # The binder validates https and keeps the secure default on a
            # non-https value, so a bad operator edit cannot point probes at a
            # plaintext endpoint that would leak the bearer token.
            bind_github_default_api_url(api_url)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="github_health_api_url",
                trigger_namespace=namespace,
                trigger_key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
