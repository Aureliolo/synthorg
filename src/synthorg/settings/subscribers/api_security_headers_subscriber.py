"""API security-headers settings subscriber.

Re-applies the operator-overridable ``/docs`` CSP origins and the RFC 9457
error-docs base URL when an operator edits ``api.csp_docs_external_origins`` or
``api.error_docs_base_url``. Both are boot-baked module globals
(``middleware._DOCS_CSP`` / ``error_taxonomy._ERROR_DOCS_BASE``) read per
response; the startup application lives in
``api.lifecycle_helpers.startup_steps.resolve_runtime_security_settings`` and
this subscriber re-invokes the same idempotent resolver so a change takes
effect within the eventual-consistency window of one in-flight response.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_NAMESPACE = "api"
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        (_NAMESPACE, "csp_docs_external_origins"),
        (_NAMESPACE, "error_docs_base_url"),
    }
)


class ApiSecurityHeadersSettingsSubscriber:
    """Re-apply the /docs CSP + error-docs base URL on a watched change.

    Both values are re-resolved together by
    ``resolve_runtime_security_settings`` (a change to either key triggers a
    full re-resolve; each key falls back to its module default independently
    on a validation failure). The resolver writes the module globals the
    middleware and error taxonomy read per response.

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
        return "api-security-headers"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Re-apply the header configuration once for the whole batch.

        The resolver re-reads both watched keys whichever one changed, so a
        batch carrying two of them would otherwise run the same re-resolve
        twice for the same result: the batch is what gets applied, not each
        pair in it.

        Args:
            changes: The watched writes to apply.
        """
        triggers = [pair for pair in changes if pair in _WATCHED]
        for namespace, key in changes:
            if (namespace, key) in _WATCHED:
                continue
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected pair",
            )
        if triggers:
            await self._apply(triggers)

    async def _apply(self, triggers: Sequence[tuple[str, str]]) -> None:
        """Re-resolve and re-apply the CSP origins + error-docs base URL.

        Args:
            triggers: The watched pairs this re-resolve answers, for the
                failure log.
        """
        from synthorg.api.lifecycle_helpers.startup_steps import (  # noqa: PLC0415
            resolve_runtime_security_settings,
        )

        try:
            await resolve_runtime_security_settings(self._app_state)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="api_security_headers",
                trigger_keys=sorted(f"{ns}.{key}" for ns, key in triggers),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
