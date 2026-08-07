"""Auth-token entropy width settings subscriber.

Applies ``security.auth_token_bytes`` to the process-wide width used for
session, refresh and websocket-ticket tokens. Tokens already issued keep
working: the width governs how the next one is minted, not how an
existing one is read.
"""

from collections.abc import Sequence

from synthorg.core.auth.token_size import set_auth_token_bytes
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_NAMESPACE = "security"
_KEY = "auth_token_bytes"
_WATCHED: frozenset[tuple[str, str]] = frozenset({(_NAMESPACE, _KEY)})


class AuthTokenSizeSettingsSubscriber:
    """Re-apply the token width when an operator changes it.

    Args:
        settings_service: Settings service the new width is read through.
    """

    def __init__(self, settings_service: SettingsService) -> None:
        self._settings_service = settings_service

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return the ``(namespace, key)`` pairs this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name for logs."""
        return "auth-token-size-settings"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Apply each changed width to subsequently minted tokens.

        Args:
            changes: The watched writes to apply.
        """
        for namespace, key in changes:
            await self._apply(namespace, key)

    async def _apply(self, namespace: str, key: str) -> None:
        """Apply the new width to subsequently minted tokens.

        A rejected value leaves the previous width in force rather than
        falling back to the default: narrowing token entropy because a
        settings read went wrong is the one outcome worth refusing, and
        the operator sees the rejection.

        Raises:
            ValueError: When the stored value is not an integer.
            Exception: Re-raised after logging so the dispatcher records
                the failure with subscriber context.
        """
        try:
            result = await self._settings_service.get(_NAMESPACE, _KEY)
            raw = str(result.value) if result.value is not None else ""
            if not raw.lstrip("+-").isdigit():
                msg = f"setting {_NAMESPACE}.{_KEY}={raw!r} is not an integer"
                raise ValueError(msg)  # noqa: TRY301 -- logged by the handler
            set_auth_token_bytes(int(raw))
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="auth_token_bytes",
                trigger_key=f"{namespace}.{key}",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            trigger_key=f"{namespace}.{key}",
            note="auth token width applied to newly minted tokens",
        )
