"""Backup settings subscriber -- react to backup setting changes."""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import compare_ci
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.settings import SETTINGS_SUBSCRIBER_NOTIFIED

if TYPE_CHECKING:
    from synthorg.backup.service import BackupService
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

# path is restart_required=True (filtered by dispatcher).
# retention_days is not watched -- read at prune time.
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("backup", "enabled"),
        ("backup", "schedule_hours"),
        ("backup", "compression"),
        ("backup", "on_shutdown"),
        ("backup", "on_startup"),
    }
)


class BackupSettingsSubscriber:
    """React to backup-namespace settings changes.

    On ``enabled`` change, starts or stops the backup scheduler.
    On ``schedule_hours`` change, reschedules the interval.
    Other keys are advisory-only (read at use time).

    Args:
        backup_service: Backup service managing the scheduler.
        settings_service: Settings service for reading current values.
    """

    def __init__(
        self,
        backup_service: BackupService,
        settings_service: SettingsService,
    ) -> None:
        self._backup_service = backup_service
        self._settings_service = settings_service

    @property
    def watched_keys(self) -> frozenset[tuple[str, str]]:
        """Return backup-namespace keys this subscriber watches."""
        return _WATCHED

    @property
    def subscriber_name(self) -> str:
        """Human-readable subscriber name."""
        return "backup-settings"

    async def on_settings_changed(
        self,
        namespace: str,
        key: str,
    ) -> None:
        """Handle a backup setting change.

        ``enabled`` toggles the scheduler. ``schedule_hours`` updates
        the interval.  Other keys are advisory and logged at INFO.

        Args:
            namespace: Changed setting namespace.
            key: Changed setting key.
        """
        if namespace != "backup":
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="ignored unexpected namespace",
            )
            return

        if key == "enabled":
            await self._toggle_scheduler()
        elif key == "schedule_hours":
            await self._reschedule()
        else:
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace=namespace,
                key=key,
                note="advisory -- read at use time",
            )

    async def _toggle_scheduler(self) -> None:
        """Start or stop the scheduler based on the current setting value."""
        try:
            result = await self._settings_service.get("backup", "enabled")
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SETTINGS_SUBSCRIBER_NOTIFIED,
                exc,
                subscriber=self.subscriber_name,
                namespace="backup",
                key="enabled",
                note="failed to read setting",
            )
            return

        scheduler = self._backup_service.scheduler
        enabled = compare_ci(str(result.value), "true")

        if enabled and not scheduler.is_running:
            try:
                await scheduler.start()
            except Exception as exc:
                reraise_critical(exc)
                # Surface a startup failure here -- without this branch
                # the exception would propagate from the subscriber
                # callback with no context tying it back to the
                # setting that triggered it. The structured log entry
                # gives the operator the namespace/key plus the
                # scrubbed error before re-raising so the dispatcher
                # still records the failure.
                log_exception_redacted(
                    logger,
                    SETTINGS_SUBSCRIBER_NOTIFIED,
                    exc,
                    subscriber=self.subscriber_name,
                    namespace="backup",
                    key="enabled",
                    note="scheduler.start() failed",
                )
                raise
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace="backup",
                key="enabled",
                note="scheduler started",
            )
        elif not enabled and scheduler.is_running:
            await scheduler.stop()
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace="backup",
                key="enabled",
                note="scheduler stopped",
            )

    async def _reschedule(self) -> None:
        """Update the scheduler interval from current settings."""
        try:
            result = await self._settings_service.get("backup", "schedule_hours")
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SETTINGS_SUBSCRIBER_NOTIFIED,
                exc,
                subscriber=self.subscriber_name,
                namespace="backup",
                key="schedule_hours",
                note="failed to read setting",
            )
            return

        scheduler = self._backup_service.scheduler
        try:
            hours = int(result.value)
        except ValueError, TypeError:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace="backup",
                key="schedule_hours",
                value=result.value,
                note="invalid schedule value",
            )
            return

        scheduler.reschedule(hours)
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            namespace="backup",
            key="schedule_hours",
            note="rescheduled",
        )
