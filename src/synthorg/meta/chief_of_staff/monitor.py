"""Async background monitor for org-level inflections.

Periodically collects a signal snapshot and compares it to the
previous snapshot using ``OrgInflectionDetector``. Detected
inflections are emitted to registered ``OrgInflectionSink``
consumers.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, ClassVar

from synthorg.core.domain_errors import ConflictError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.chief_of_staff import (
    COS_INFLECTION_CHECK_FAILED,
    COS_INFLECTION_DETECTED,
    COS_MONITOR_LOOP_DIED,
    COS_MONITOR_STARTED,
    COS_MONITOR_STOPPED,
)

if TYPE_CHECKING:
    from synthorg.meta.chief_of_staff.inflection import OrgInflectionDetector
    from synthorg.meta.chief_of_staff.models import OrgInflection
    from synthorg.meta.chief_of_staff.protocol import OrgInflectionSink
    from synthorg.meta.models import OrgSignalSnapshot
    from synthorg.meta.signals.snapshot import SnapshotBuilder

logger = get_logger(__name__)


class InflectionMonitorLifecycleError(ConflictError):
    """Raised when ``OrgInflectionMonitor.start()`` is called after a timed-out stop.

    Mirrors :class:`BackupUnrestartableError`: a stuck drain leaves an
    orphan loop the new instance would race; the canonical lifecycle
    pattern marks the monitor unrestartable so operators must construct
    a fresh one.
    """

    default_message: ClassVar[str] = (
        "OrgInflectionMonitor is unrestartable after a timed-out stop"
    )


class OrgInflectionMonitor:
    """Background loop for org-level inflection detection.

    Collects snapshots at a configurable interval and emits
    ``OrgInflection`` events to registered sinks when metrics
    change beyond detection thresholds.

    Args:
        detector: Inflection detector instance.
        snapshot_builder: Builder for org signal snapshots.
        sinks: Consumers of detected inflections.
        check_interval_minutes: Minutes between checks.
    """

    def __init__(
        self,
        *,
        detector: OrgInflectionDetector,
        snapshot_builder: SnapshotBuilder,
        sinks: tuple[OrgInflectionSink, ...],
        check_interval_minutes: int = 15,
    ) -> None:
        if check_interval_minutes < 1:
            msg = f"check_interval_minutes must be >= 1, got {check_interval_minutes}"
            raise ValueError(msg)
        self._detector = detector
        self._builder = snapshot_builder
        self._sinks = sinks
        self._interval_s = check_interval_minutes * 60
        self._last_snapshot: OrgSignalSnapshot | None = None
        self._task: asyncio.Task[None] | None = None
        # Per ``docs/reference/lifecycle-sync.md`` the lifecycle
        # primitives are constructed eagerly so a racing ``stop()``
        # cannot observe a half-published lock attribute.
        self._stop_event: asyncio.Event = asyncio.Event()
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()
        self._stop_failed: bool = False
        self._stop_drain_timeout_seconds: float = 30.0

    async def start(self) -> None:
        """Start the background monitoring loop.

        Idempotent + concurrent-safe per the canonical lifecycle
        pattern: serialises on ``self._lifecycle_lock`` so concurrent
        callers cannot both observe ``_task is None`` and double-spawn.
        """
        async with self._lifecycle_lock:
            if self._stop_failed:
                msg = (
                    "OrgInflectionMonitor is unrestartable after a "
                    "timed-out stop; construct a fresh monitor instead"
                )
                logger.warning(
                    COS_INFLECTION_CHECK_FAILED,
                    error=msg,
                    note="unrestartable",
                )
                raise InflectionMonitorLifecycleError(msg)
            if self._task is not None and not self._task.done():
                return
            self._stop_event.clear()
            self._task = asyncio.create_task(
                self._loop(),
                name="cos-monitor-loop",
            )
            self._task.add_done_callback(
                log_task_exceptions(logger, COS_MONITOR_LOOP_DIED),
            )
            logger.info(
                COS_MONITOR_STARTED,
                interval_minutes=self._interval_s // 60,
            )

    async def stop(self) -> None:
        """Stop the monitoring loop gracefully.

        Holds ``self._lifecycle_lock`` so a concurrent ``start()``
        cannot recreate the task mid-stop. Drain is shielded with a
        hard deadline; on timeout the monitor is marked unrestartable.
        """
        async with self._lifecycle_lock:
            self._stop_event.set()
            task = self._task
            if task is None:
                return
            try:
                # Cooperative drain: ``_stop_event`` is already set,
                # so the loop wakes from its ``wait_for`` and exits.
                # ``shield`` keeps the drain timeout from cancelling
                # an in-flight check that's about to terminate
                # naturally. No helper task is created -- a direct
                # await means there is no orphan wrapper to leak when
                # ``wait_for`` times out.
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=self._stop_drain_timeout_seconds,
                )
            except TimeoutError:
                # Cooperative drain missed the deadline. Cancel hard
                # and mark the monitor unrestartable so a later
                # ``start()`` cannot stack a second loop on top of an
                # orphan task that may still own snapshot state.
                task.cancel()
                self._stop_failed = True
                logger.error(  # noqa: TRY400
                    COS_INFLECTION_CHECK_FAILED,
                    error=("stop exceeded hard deadline; monitor marked unrestartable"),
                    timeout_seconds=self._stop_drain_timeout_seconds,
                )
                raise
            except asyncio.CancelledError:
                # Loop was already cancelled before observing
                # ``_stop_event``; treat as drained successfully.
                pass
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                logger.warning(
                    COS_INFLECTION_CHECK_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    note="shutdown",
                )
            self._task = None
            self._last_snapshot = None
            # Recreate the loop-bound stop event WHILE holding the
            # lifecycle lock. Outside the lock, a racing ``start()``
            # could spawn a monitor task bound to the OLD event
            # before this assignment lands, leaving a later stop()
            # signalling a different event than the task is waiting
            # on. ``self._lifecycle_lock`` itself MUST stay the same
            # instance for the service lifetime; only the event is
            # swapped.
            self._stop_event = asyncio.Event()
            logger.info(COS_MONITOR_STOPPED)

    async def _loop(self) -> None:
        """Periodic snapshot collection and inflection check.

        Uses ``wait_for(_stop_event.wait(), timeout=interval)`` instead
        of plain ``asyncio.sleep`` so cancellation cooperatively wakes
        the loop and the canonical drain timeout has a chance to
        complete the shutdown promptly.
        """
        while not self._stop_event.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.exception(COS_INFLECTION_CHECK_FAILED)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval_s,
                )
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise

    async def _tick(self) -> None:
        """Single monitoring tick."""
        now = datetime.now(UTC)
        since = now - timedelta(seconds=self._interval_s)
        current = await self._builder.build(since=since, until=now)
        if self._last_snapshot is None:
            self._last_snapshot = current
            return
        inflections = await self._detector.detect(
            previous=self._last_snapshot,
            current=current,
        )
        self._last_snapshot = current
        for inflection in inflections:
            logger.info(
                COS_INFLECTION_DETECTED,
                metric=inflection.metric_name,
                severity=inflection.severity.value,
                old_value=inflection.old_value,
                new_value=inflection.new_value,
            )
            await self._emit_to_sinks(inflection)

    async def _emit_to_sinks(self, inflection: OrgInflection) -> None:
        """Emit an inflection to all sinks in parallel."""

        async def _emit(sink: OrgInflectionSink) -> None:
            try:
                await sink.on_inflection(inflection)
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.exception(
                    COS_INFLECTION_CHECK_FAILED,
                    sink=type(sink).__name__,
                )

        async with asyncio.TaskGroup() as tg:
            for sink in self._sinks:
                tg.create_task(_emit(sink))
