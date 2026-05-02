"""Backup scheduler -- periodic background backup task."""

import asyncio
from typing import TYPE_CHECKING

from synthorg.backup.models import BackupTrigger
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.backup import (
    BACKUP_FAILED,
    BACKUP_SCHEDULER_RESCHEDULED,
    BACKUP_SCHEDULER_STARTED,
    BACKUP_SCHEDULER_STOPPED,
    BACKUP_SCHEDULER_TICK,
)

if TYPE_CHECKING:
    from synthorg.backup.service import BackupService

logger = get_logger(__name__)


class BackupScheduler:
    """Background asyncio task that triggers periodic backups.

    Args:
        service: Backup service to delegate backup creation to.
        interval_hours: Hours between scheduled backups.
    """

    def __init__(self, service: BackupService, interval_hours: int) -> None:
        self._service = service
        self._interval_seconds = interval_hours * 3600
        self._task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()
        # Per ``docs/reference/lifecycle-sync.md``: dedicated
        # lifecycle primitives, drain timeout, unrestartable flag.
        self._stop_event: asyncio.Event = asyncio.Event()
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()
        self._stop_failed: bool = False
        self._stop_drain_timeout_seconds: float = 30.0

    @property
    def is_running(self) -> bool:
        """Whether the scheduler loop is currently active."""
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start the background scheduler loop.

        Creates an ``asyncio.Task`` running ``_run_loop``.  Idempotent
        + concurrent-safe per the canonical lifecycle pattern.
        Refuses to start if a previous ``stop()`` exceeded the drain
        deadline (the orphan task may still own the backup lock).
        """
        async with self._lifecycle_lock:
            if self._stop_failed:
                msg = (
                    "BackupScheduler is unrestartable after a "
                    "timed-out stop; construct a fresh scheduler instead"
                )
                logger.warning(
                    BACKUP_FAILED,
                    error=msg,
                    note="unrestartable",
                )
                raise RuntimeError(msg)
            if self.is_running:
                return
            self._wake_event.clear()
            self._stop_event.clear()
            self._task = asyncio.create_task(
                self._run_loop(),
                name="backup-scheduler",
            )
            logger.info(
                BACKUP_SCHEDULER_STARTED,
                interval_hours=self._interval_seconds // 3600,
            )

    async def stop(self) -> None:
        """Cancel the background scheduler and wait for it to finish.

        Drain is shielded with a hard deadline; on timeout the
        scheduler is marked unrestartable so a subsequent ``start()``
        cannot stack a second loop on top of an orphan task.
        """
        async with self._lifecycle_lock:
            self._stop_event.set()
            self._wake_event.set()
            task = self._task
            if task is None:
                return
            task.cancel()

            async def _drain() -> None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except MemoryError, RecursionError:
                    raise
                except Exception as exc:
                    logger.warning(
                        BACKUP_FAILED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                        note="shutdown",
                    )

            drain_task: asyncio.Task[None] = asyncio.create_task(_drain())
            try:
                await asyncio.wait_for(
                    asyncio.shield(drain_task),
                    timeout=self._stop_drain_timeout_seconds,
                )
            except TimeoutError:
                self._stop_failed = True
                logger.error(  # noqa: TRY400
                    BACKUP_FAILED,
                    error=(
                        "stop exceeded hard deadline; scheduler marked unrestartable"
                    ),
                    timeout_seconds=self._stop_drain_timeout_seconds,
                )
                raise
            self._task = None
            logger.info(BACKUP_SCHEDULER_STOPPED)
        # Recreate primitives outside the (released) lock so a
        # subsequent ``start()`` on a different event loop can rebind.
        self._lifecycle_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()

    def reschedule(self, interval_hours: int) -> None:
        """Update the interval and interrupt the current sleep.

        The new interval takes effect immediately by waking the
        sleeping loop.

        Args:
            interval_hours: New interval in hours (must be >= 1).

        Raises:
            ValueError: If interval_hours is less than 1.
        """
        if interval_hours < 1:
            msg = "interval_hours must be >= 1"
            raise ValueError(msg)
        self._interval_seconds = interval_hours * 3600
        self._wake_event.set()
        logger.info(
            BACKUP_SCHEDULER_RESCHEDULED,
            interval_hours=interval_hours,
        )

    async def _run_loop(self) -> None:
        """Sleep-and-backup loop.

        Honors ``self._stop_event``: when set, the loop exits cleanly
        without firing another backup. ``self._wake_event`` still
        interrupts the sleep for ``reschedule()``.
        """
        while not self._stop_event.is_set():
            self._wake_event.clear()
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._interval_seconds,
                )
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            if self._stop_event.is_set():
                return
            logger.debug(BACKUP_SCHEDULER_TICK)
            try:
                await self._service.create_backup(BackupTrigger.SCHEDULED)
            except MemoryError, RecursionError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    BACKUP_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    note="scheduled_run",
                )
