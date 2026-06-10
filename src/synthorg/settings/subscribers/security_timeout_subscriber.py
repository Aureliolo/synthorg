"""Security timeout settings subscriber -- live reschedule on interval changes.

Watches ``security.timeout_check_interval_seconds`` and calls
``ApprovalTimeoutScheduler.reschedule(...)`` so operator overrides
take effect on the next scheduler tick without restart. The
startup-time application of the same setting lives in
``synthorg.api.lifecycle_helpers._apply_security_timeout_interval``.
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.settings import SETTINGS_SUBSCRIBER_NOTIFIED
from synthorg.settings.service import SettingsService

if TYPE_CHECKING:
    # Cycle breaker: the security package reads settings at runtime, so
    # the scheduler is named for signatures only.
    from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler

logger = get_logger(__name__)

_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {("security", "timeout_check_interval_seconds")},
)


class SecurityTimeoutSettingsSubscriber:
    """React to ``security.timeout_check_interval_seconds`` changes.

    Reads the new interval via ``SettingsService`` and reschedules the
    scheduler. Read failures and parse failures log + skip; the
    scheduler keeps its current interval. Mirrors the
    ``BackupSettingsSubscriber._reschedule`` discipline.

    Args:
        scheduler: The approval timeout scheduler to reschedule.
        settings_service: Settings service for reading the current value.
    """

    def __init__(
        self,
        *,
        scheduler: ApprovalTimeoutScheduler,
        settings_service: SettingsService,
    ) -> None:
        self._scheduler = scheduler
        self._settings_service = settings_service

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return security-namespace keys this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name."""
        return "security-timeout-settings"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Handle a change to the timeout-check-interval setting.

        Args:
            namespace: Changed setting namespace.
            key: Changed setting key.
        """
        if (namespace, key) not in _WATCHED:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected key",
            )
            return

        try:
            result = await self._settings_service.get(namespace, key)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SETTINGS_SUBSCRIBER_NOTIFIED,
                exc,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="failed to read setting",
            )
            return

        try:
            interval = float(result.value)
        except (ValueError, TypeError) as exc:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                value=result.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="invalid interval value",
            )
            return

        try:
            self._scheduler.reschedule(interval)
        except ValueError as exc:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                value=interval,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="scheduler rejected interval",
            )
            return

        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            namespace=namespace,
            key=key,
            note="rescheduled",
        )
