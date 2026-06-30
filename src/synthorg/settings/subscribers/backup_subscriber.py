"""Backup settings subscriber -- react to backup setting changes."""

from synthorg.backup.service import BackupService
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.normalization import compare_ci
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.settings import (
    SETTINGS_SERVICE_SWAP_FAILED,
    SETTINGS_SUBSCRIBER_NOTIFIED,
)
from synthorg.settings.service import SettingsService

logger = get_logger(__name__)

# retention_days is not watched -- read at prune time.
_WATCHED: frozenset[tuple[str, str]] = frozenset(
    {
        ("backup", "enabled"),
        ("backup", "schedule_hours"),
        ("backup", "path"),
        ("backup", "compression"),
        ("backup", "on_shutdown"),
        ("backup", "on_startup"),
    }
)


class BackupSettingsSubscriber:
    """React to backup-namespace settings changes.

    On ``enabled`` change, starts or stops the backup scheduler. On
    ``schedule_hours`` change, reschedules the interval. On ``path`` change,
    re-points the write + retention scan directory. On ``compression`` /
    ``on_shutdown`` / ``on_startup`` change, hot-replaces the flag on the
    service's frozen ``BackupConfig`` so it applies without a restart.
    ``retention_days`` is not watched: the retention manager re-reads it from
    the resolver at every prune.

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
        elif key == "path":
            await self._apply_path()
        elif key in ("compression", "on_shutdown", "on_startup"):
            await self._apply_flag(key)
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
            # Re-raise after logging: a swallowed read failure would let the
            # dispatcher record SETTINGS_SUBSCRIBER_NOTIFIED (apparent success)
            # even though the scheduler was never toggled. Propagating makes
            # the dispatcher log SETTINGS_SUBSCRIBER_ERROR instead, matching
            # the action-failure paths below; the dispatcher loop still
            # continues (it catches per-subscriber).
            log_exception_redacted(
                logger,
                SETTINGS_SERVICE_SWAP_FAILED,
                exc,
                subscriber=self.subscriber_name,
                namespace="backup",
                key="enabled",
                note="failed to read setting",
            )
            raise

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
                    SETTINGS_SERVICE_SWAP_FAILED,
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
            try:
                await scheduler.stop()
            except Exception as exc:
                reraise_critical(exc)
                # Symmetric with the start() arm above: tie a stop() failure
                # back to the triggering setting before re-raising so the
                # dispatcher records it with context.
                log_exception_redacted(
                    logger,
                    SETTINGS_SERVICE_SWAP_FAILED,
                    exc,
                    subscriber=self.subscriber_name,
                    namespace="backup",
                    key="enabled",
                    note="scheduler.stop() failed",
                )
                raise
            logger.info(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace="backup",
                key="enabled",
                note="scheduler stopped",
            )

    async def _apply_path(self) -> None:
        """Push a changed ``backup.path`` onto the service + retention manager."""
        try:
            result = await self._settings_service.get("backup", "path")
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SETTINGS_SERVICE_SWAP_FAILED,
                exc,
                subscriber=self.subscriber_name,
                namespace="backup",
                key="path",
                note="failed to read setting",
            )
            raise
        path = str(result.value).strip()
        if not path:
            logger.warning(
                SETTINGS_SUBSCRIBER_NOTIFIED,
                subscriber=self.subscriber_name,
                namespace="backup",
                key="path",
                note="blank path ignored",
            )
            return
        try:
            await self._backup_service.set_backup_path(path)
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SETTINGS_SERVICE_SWAP_FAILED,
                exc,
                subscriber=self.subscriber_name,
                namespace="backup",
                key="path",
                note="set_backup_path() failed",
            )
            raise
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            namespace="backup",
            key="path",
            note="backup path updated",
        )

    async def _apply_flag(self, key: str) -> None:
        """Resolve a boolean backup flag and push it onto the live service.

        Covers ``compression`` / ``on_shutdown`` / ``on_startup``: each lives on
        the service's frozen ``BackupConfig``, so the value is hot-replaced via
        ``BackupService.apply_config_flag`` rather than left for a restart.
        ``compression`` then applies to the next backup and ``on_shutdown`` to
        the next graceful shutdown; ``on_startup`` updates the in-memory config
        and takes operational effect on the next process start.
        """
        try:
            result = await self._settings_service.get("backup", key)
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SETTINGS_SERVICE_SWAP_FAILED,
                exc,
                subscriber=self.subscriber_name,
                namespace="backup",
                key=key,
                note="failed to read setting",
            )
            raise
        value = compare_ci(str(result.value), "true")
        try:
            await self._backup_service.apply_config_flag(key, value=value)
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SETTINGS_SERVICE_SWAP_FAILED,
                exc,
                subscriber=self.subscriber_name,
                namespace="backup",
                key=key,
                note="apply_config_flag() failed",
            )
            raise
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            namespace="backup",
            key=key,
            note="backup config flag updated",
        )

    async def _reschedule(self) -> None:
        """Update the scheduler interval from current settings."""
        try:
            result = await self._settings_service.get("backup", "schedule_hours")
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                SETTINGS_SERVICE_SWAP_FAILED,
                exc,
                subscriber=self.subscriber_name,
                namespace="backup",
                key="schedule_hours",
                note="failed to read setting",
            )
            raise

        scheduler = self._backup_service.scheduler
        try:
            hours = int(result.value)
        except (ValueError, TypeError) as exc:
            logger.warning(
                SETTINGS_SERVICE_SWAP_FAILED,
                subscriber=self.subscriber_name,
                namespace="backup",
                key="schedule_hours",
                value=result.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="invalid schedule value",
            )
            return

        try:
            scheduler.reschedule(hours)
        except Exception as exc:
            reraise_critical(exc)
            # Symmetric with the start()/stop() arms: tie a reschedule failure
            # back to the triggering setting before re-raising.
            log_exception_redacted(
                logger,
                SETTINGS_SERVICE_SWAP_FAILED,
                exc,
                subscriber=self.subscriber_name,
                namespace="backup",
                key="schedule_hours",
                note="scheduler.reschedule() failed",
            )
            raise
        logger.info(
            SETTINGS_SUBSCRIBER_NOTIFIED,
            subscriber=self.subscriber_name,
            namespace="backup",
            key="schedule_hours",
            note="rescheduled",
        )
