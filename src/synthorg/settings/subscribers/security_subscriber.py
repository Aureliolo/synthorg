"""Security settings subscriber -- hot-reload discovery allowlist.

Watches the ``providers/discovery_allowlist`` setting and rebuilds
the ``ProviderDiscoveryPolicy`` when it changes.
"""

import json
from collections.abc import Awaitable, Callable

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.security import (
    SECURITY_ALLOWLIST_UPDATE_FAILED,
    SECURITY_ALLOWLIST_UPDATED,
)
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {("providers", "discovery_allowlist")},
)


class SecuritySubscriber:
    """React to ``providers/discovery_allowlist`` changes.

    Reads the updated allowlist value from the settings service,
    parses the JSON list, and invokes the callback to rebuild
    the provider discovery policy.

    Args:
        settings_service: Settings service for reading current values.
        on_allowlist_changed: Async callback receiving the parsed
            ``host:port`` tuple.  Typically rebuilds
            ``ProviderDiscoveryPolicy`` and swaps it into app state.
    """

    def __init__(
        self,
        *,
        settings_service: SettingsService,
        on_allowlist_changed: Callable[[tuple[str, ...]], Awaitable[None]],
    ) -> None:
        self._settings_service = settings_service
        self._on_changed = on_allowlist_changed

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Keys this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name."""
        return "security-discovery-allowlist"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Handle a change to the discovery allowlist setting.

        Reads the current value, parses the JSON-encoded list,
        and invokes the callback to rebuild the policy.

        Args:
            namespace: Setting namespace (expected ``"providers"``).
            key: Setting key (expected ``"discovery_allowlist"``).
        """
        if (namespace, key) not in _WATCHED:
            return

        try:
            setting = await self._settings_service.get(namespace, key)
            raw = setting.value if setting is not None else "[]"
            entries = json.loads(raw)
            if not isinstance(entries, list):
                logger.warning(
                    SECURITY_ALLOWLIST_UPDATE_FAILED,
                    namespace=namespace,
                    key=key,
                    error="expected JSON array",
                )
                return
            if any(
                not isinstance(entry, str) or not entry.strip() for entry in entries
            ):
                logger.warning(
                    SECURITY_ALLOWLIST_UPDATE_FAILED,
                    namespace=namespace,
                    key=key,
                    error="allowlist entries must be non-empty strings",
                )
                return
            allowlist = tuple(entry.strip() for entry in entries)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                SECURITY_ALLOWLIST_UPDATE_FAILED,
                namespace=namespace,
                key=key,
                context="failed to parse allowlist",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return

        try:
            await self._on_changed(allowlist)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SECURITY_ALLOWLIST_UPDATE_FAILED,
                namespace=namespace,
                key=key,
                context="failed to apply allowlist",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            SECURITY_ALLOWLIST_UPDATED,
            namespace=namespace,
            key=key,
            count=len(allowlist),
        )
