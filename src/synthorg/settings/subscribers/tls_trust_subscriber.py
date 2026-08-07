"""Outbound TLS trust settings subscriber.

Keeps the process-wide snapshot in :mod:`synthorg.core.tls_trust` matching
``security.tls_ca_bundle`` / ``security.tls_verify``. The snapshot exists
because the git path builds its child environment synchronously with no
resolver in reach (see that module's docstring); this subscriber is what
makes the pair live rather than boot-baked, so an operator who adds their
internal CA sees the next clone and the next forge API call use it.

Both keys are re-read together whichever one changed: they are one
configuration, and applying half of it would leave a bundle installed with
verification already off, or the reverse.
"""

from collections.abc import Sequence

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.tls_trust import TlsTrust, set_tls_trust
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

_NAMESPACE = "security"
_CA_BUNDLE_KEY = "tls_ca_bundle"
_VERIFY_KEY = "tls_verify"
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        (_NAMESPACE, _CA_BUNDLE_KEY),
        (_NAMESPACE, _VERIFY_KEY),
    }
)


class TlsTrustSettingsSubscriber:
    """Re-install the outbound TLS trust snapshot on a watched change.

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
        return "tls-trust"

    async def on_settings_changed(self, changes: Sequence[tuple[str, str]]) -> None:
        """Re-resolve the trust pair once for the whole batch.

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
        """Read both keys and install the resulting snapshot.

        Args:
            triggers: The watched pairs this re-resolve answers, for the
                failure log.
        """
        from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

        try:
            resolver = config_resolver_of(self._app_state)
            trust = TlsTrust(
                ca_bundle=await resolver.get_str(_NAMESPACE, _CA_BUNDLE_KEY),
                verify=await resolver.get_bool(_NAMESPACE, _VERIFY_KEY),
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                service="tls_trust",
                trigger_keys=sorted(f"{ns}.{key}" for ns, key in triggers),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        set_tls_trust(trust)
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            # The path is operator-supplied configuration, not a secret, and
            # whether verification is on is exactly what an audit needs.
            ca_bundle_configured=bool(trust.ca_bundle),
            verify=trust.verify,
        )


__all__ = ["TlsTrustSettingsSubscriber"]
