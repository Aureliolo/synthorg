"""Backup scheduler -- periodic background backup task."""

import asyncio
import contextlib
from typing import TYPE_CHECKING

from synthorg.backup.errors import BackupUnrestartableError
from synthorg.backup.models import BackupTrigger
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import log_task_exceptions
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
        # Loop-bound asyncio primitives are deferred until ``start()``
        # so the scheduler can be safely re-started on a different
        # event loop.  See ApprovalTimeoutScheduler for the canonical
        # rationale (test scenarios where pytest-asyncio creates a
        # fresh per-test loop while a session-scoped Litestar app
        # holds this instance).
        self._wake_event: asyncio.Event | None = None
        self._stop_event: asyncio.Event | None = None
        self._lifecycle_lock: asyncio.Lock | None = None
        self._stop_failed: bool = False
        self._stop_drain_timeout_seconds: float = 30.0

    def _task_is_on_current_loop(self) -> bool:
        """True iff the existing task is alive on the current loop.

        Used internally by ``start()`` to detect cross-loop reuse.
        Distinct from :attr:`is_running` so the public predicate stays
        cheap and mockable in tests; loop introspection only matters
        on the cross-loop-restart path.

        Returns ``True`` (i.e. "do not drop state") when the task or
        loop cannot be introspected -- typically a ``MagicMock(spec=
        asyncio.Task)`` in tests where ``get_loop()`` returns a mock.
        Erring on the side of "same loop" prevents spurious task drops
        in unit tests; the genuine cross-loop scenario in production
        always returns a real ``AbstractEventLoop``.
        """
        if self._task is None or self._task.done():
            return False
        try:
            # ``object`` annotation defeats mypy's narrowing of
            # ``Task.get_loop`` so the runtime ``isinstance`` check
            # below is reachable for ``MagicMock(spec=Task)`` test
            # fixtures whose ``get_loop`` returns a mock value.
            task_loop: object = self._task.get_loop()
        except RuntimeError, AttributeError:
            return True
        if not isinstance(task_loop, asyncio.AbstractEventLoop):
            return True
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            return True
        return task_loop is current

    def _drop_stale_loop_state(self) -> None:
        """Discard task and primitives bound to a closed-or-other loop."""
        self._task = None
        self._wake_event = None
        self._stop_event = None
        self._lifecycle_lock = None

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

        When a previous ``start()`` ran on a different (now-closed)
        loop, the stale task and its loop-bound primitives are
        discarded and fresh ones are spawned on the current loop.
        """
        # Detect cross-loop reuse before touching any lifecycle primitive.
        if self._task is not None and not self._task_is_on_current_loop():
            self._drop_stale_loop_state()
        if self._lifecycle_lock is None:
            self._lifecycle_lock = asyncio.Lock()
        if self._wake_event is None:
            self._wake_event = asyncio.Event()
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
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
                raise BackupUnrestartableError(msg)
            if self.is_running:
                return
            self._wake_event.clear()
            self._stop_event.clear()
            self._task = asyncio.create_task(
                self._run_loop(),
                name="backup-scheduler",
            )
            # Surface unexpected loop deaths -- without this callback
            # an exception inside ``_run_loop`` would set the task to
            # ``done`` silently and ``is_running`` would flip to False
            # without anyone noticing the scheduled backups stopped.
            self._task.add_done_callback(
                log_task_exceptions(logger, BACKUP_FAILED, note="scheduler_loop_died"),
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

        Tolerant of partial state: if a caller (e.g. unit tests)
        attached a task without going through ``start()``, the events
        and lifecycle lock may be ``None``.  In that case stop()
        cancels the task directly via ``task.cancel()`` without
        signalling, since there is no event the running loop is
        waiting on.
        """
        if self._task is None:
            return
        if (
            self._lifecycle_lock is None
            or self._stop_event is None
            or self._wake_event is None
        ):
            # Inconsistent state (test fixture set _task directly,
            # or lifecycle was already torn down).  Cancel the task
            # without signalling; there is no event to wake.
            task = self._task
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._task = None
            return
        async with self._lifecycle_lock:
            self._stop_event.set()
            self._wake_event.set()
            task = self._task
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
            # Drop loop-bound primitives so the next ``start()`` rebinds
            # them to whichever loop is current then.  Required for
            # tests that span multiple event loops (pytest-asyncio
            # function-scoped loops): keeping the same instance here
            # would leave the events bound to the closed loop.
            self._stop_event = None
            self._wake_event = None
            self._lifecycle_lock = None
            logger.info(BACKUP_SCHEDULER_STOPPED)

    def reschedule(self, interval_hours: int) -> None:
        """Update the interval and interrupt the current sleep.

        The new interval takes effect immediately by waking the
        sleeping loop.  When the scheduler hasn't started yet the
        new interval lands on the next ``start()``.

        Args:
            interval_hours: New interval in hours (must be >= 1).

        Raises:
            ValueError: If interval_hours is less than 1.
        """
        if interval_hours < 1:
            msg = "interval_hours must be >= 1"
            raise ValueError(msg)
        self._interval_seconds = interval_hours * 3600
        if self._wake_event is not None:
            self._wake_event.set()
        logger.info(
            BACKUP_SCHEDULER_RESCHEDULED,
            interval_hours=interval_hours,
        )

    async def _run_loop(self) -> None:
        """Sleep-and-backup loop.

        Honors ``self._stop_event``: when set, the loop exits cleanly
        without firing another backup. ``self._wake_event`` still
        interrupts the sleep for ``reschedule()``.  Captures both
        events into locals because ``stop()`` may set them to ``None``
        on completion; ``start()`` guarantees they are non-None at
        the moment the task is spawned.
        """
        wake_event = self._wake_event
        stop_event = self._stop_event
        if wake_event is None or stop_event is None:  # defensive
            msg = "_run_loop invoked without initialised lifecycle events"
            raise RuntimeError(msg)
        while not stop_event.is_set():
            wake_event.clear()
            try:
                await asyncio.wait_for(
                    wake_event.wait(),
                    timeout=self._interval_seconds,
                )
            except TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            if stop_event.is_set():
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
