"""Notification dispatcher -- fan-out to registered sinks."""

import asyncio
import contextlib
from typing import TYPE_CHECKING

from synthorg.notifications.models import (
    Notification,
    NotificationSeverity,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.notification import (
    NOTIFICATION_DISPATCH_FAILED,
    NOTIFICATION_DISPATCHED,
    NOTIFICATION_DISPATCHER_CLOSED,
    NOTIFICATION_DISPATCHER_STARTED,
    NOTIFICATION_FILTERED,
    NOTIFICATION_NO_SINKS,
    NOTIFICATION_SINK_CLOSE_FAILED,
    NOTIFICATION_SINK_REGISTERED,
    NOTIFICATION_SINK_START_FAILED,
)

if TYPE_CHECKING:
    from synthorg.notifications.protocol import NotificationSink

logger = get_logger(__name__)

_SEVERITY_ORDER: dict[NotificationSeverity, int] = {
    NotificationSeverity.INFO: 0,
    NotificationSeverity.WARNING: 1,
    NotificationSeverity.ERROR: 2,
    NotificationSeverity.CRITICAL: 3,
}


def _ignore_value_error() -> contextlib.AbstractContextManager[None]:
    """Suppress ``ValueError`` from list operations on a possibly-stale sink."""
    return contextlib.suppress(ValueError)


class NotificationDispatcher:
    """Fan-out notifications to all registered sinks.

    Best-effort delivery: individual sink failures are logged and
    swallowed. Uses ``asyncio.TaskGroup`` for concurrent delivery.

    Notifications below ``min_severity`` are silently filtered.

    ``register()`` is only safe to call before the event loop
    starts processing requests.

    Args:
        sinks: Initial set of notification sinks.
        min_severity: Minimum severity to dispatch.
    """

    __slots__ = (
        "_dispatch_idle",
        "_dispatch_inflight",
        "_lifecycle_lock",
        "_min_severity",
        "_sinks",
        "_started",
        "_stopping",
    )

    def __init__(
        self,
        sinks: tuple[NotificationSink, ...] = (),
        *,
        min_severity: NotificationSeverity = NotificationSeverity.INFO,
    ) -> None:
        self._sinks = list(sinks)
        self._min_severity = min_severity
        self._lifecycle_lock = asyncio.Lock()
        self._started = False
        # Dispatch gate: ``aclose`` flips ``_stopping`` so any
        # ``dispatch`` that arrives during shutdown short-circuits
        # before touching ``sink.send``; ``_dispatch_inflight`` +
        # ``_dispatch_idle`` let ``aclose`` wait for in-flight sends
        # to drain before closing sinks. The counter / event pair
        # is mutated only between ``await`` points (single-threaded
        # asyncio), so no separate lock is needed.
        self._stopping = False
        self._dispatch_inflight = 0
        self._dispatch_idle = asyncio.Event()
        self._dispatch_idle.set()
        for sink in sinks:
            logger.info(
                NOTIFICATION_SINK_REGISTERED,
                sink_name=sink.sink_name,
            )

    def register(self, sink: NotificationSink) -> None:
        """Register an additional sink.

        Only safe before ``start()``; sinks added afterwards are
        handed straight to ``dispatch()`` without their ``start()``
        being invoked.

        Args:
            sink: Notification sink to add.
        """
        self._sinks.append(sink)
        logger.info(
            NOTIFICATION_SINK_REGISTERED,
            sink_name=sink.sink_name,
        )

    async def start(self) -> None:
        """Start every registered sink (idempotent).

        Fans out ``sink.start()`` across registered sinks via
        ``asyncio.TaskGroup``. A single sink failing its start is
        dropped from the active set so subsequent ``dispatch()``
        calls skip it; one bad sink does not abort the whole group.
        """
        async with self._lifecycle_lock:
            if self._started:
                return
            sinks = list(self._sinks)
            failed: list[NotificationSink] = []
            async with asyncio.TaskGroup() as tg:
                for sink in sinks:
                    tg.create_task(self._safe_start(sink, failed))
            for sink in failed:
                with _ignore_value_error():
                    self._sinks.remove(sink)
            self._started = True
            logger.info(
                NOTIFICATION_DISPATCHER_STARTED,
                sinks=len(self._sinks),
                failed=len(failed),
            )

    async def aclose(self) -> None:
        """Close every registered sink (idempotent).

        Flips ``_stopping`` so ``dispatch()`` calls that arrive
        during shutdown short-circuit before invoking ``sink.send``,
        then waits for any in-flight sends to drain before tearing
        down the sinks. This prevents the use-after-close window
        where ``dispatch`` could call ``sink.send`` while
        ``_safe_close`` is closing the same sink's underlying client.
        """
        async with self._lifecycle_lock:
            if not self._started:
                return
            self._stopping = True
            await self._dispatch_idle.wait()
            sinks = list(self._sinks)
            async with asyncio.TaskGroup() as tg:
                for sink in sinks:
                    tg.create_task(self._safe_close(sink))
            self._started = False
            logger.info(
                NOTIFICATION_DISPATCHER_CLOSED,
                sinks=len(sinks),
            )

    @staticmethod
    async def _safe_start(
        sink: NotificationSink,
        failed: list[NotificationSink],
    ) -> None:
        """Run ``sink.start()`` with per-sink error isolation.

        Catches every exception except ``MemoryError`` /
        ``RecursionError`` so one failing sink does not abort the
        TaskGroup. Adds the sink to ``failed`` so the outer
        ``start()`` can drop it from the active set.
        """
        try:
            await sink.start()
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            failed.append(sink)
            logger.warning(
                NOTIFICATION_SINK_START_FAILED,
                sink_name=sink.sink_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    @staticmethod
    async def _safe_close(sink: NotificationSink) -> None:
        """Run ``sink.close()`` with per-sink error isolation."""
        try:
            await sink.close()
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                NOTIFICATION_SINK_CLOSE_FAILED,
                sink_name=sink.sink_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def dispatch(self, notification: Notification) -> None:
        """Deliver a notification to all registered sinks.

        Best-effort: individual sink errors are logged and
        swallowed. ``MemoryError`` and ``RecursionError`` propagate.

        Args:
            notification: The notification to deliver.
        """
        # Shutdown gate: once ``aclose`` flips ``_stopping``, no new
        # dispatches reach the sinks. The check is sync so it cannot
        # interleave with ``aclose`` between this point and the
        # counter bump below.
        if self._stopping:
            logger.debug(
                NOTIFICATION_FILTERED,
                notification_id=notification.id,
                detail="dispatcher_stopping",
            )
            return
        # Snapshot the sink list so register() during dispatch is safe.
        sinks = list(self._sinks)
        if not sinks:
            logger.debug(
                NOTIFICATION_NO_SINKS,
                notification_id=notification.id,
            )
            return

        if self._should_filter(notification):
            return

        # Track in-flight dispatch so ``aclose`` can wait for the
        # send fan-out to finish before closing sinks. Counter +
        # event pair mutated only between awaits (single-threaded
        # asyncio); no separate lock needed.
        self._dispatch_inflight += 1
        self._dispatch_idle.clear()
        errors: list[str | None] = [None] * len(sinks)
        try:
            async with asyncio.TaskGroup() as tg:
                for idx, sink in enumerate(sinks):
                    tg.create_task(
                        self._guarded_send(sink, notification, errors, idx),
                    )
        except ExceptionGroup as eg:
            for exc in eg.exceptions:
                if isinstance(exc, MemoryError | RecursionError):
                    raise exc from eg
            self._log_exception_group(notification, errors, eg)
            return
        finally:
            self._dispatch_inflight -= 1
            if self._dispatch_inflight == 0:
                self._dispatch_idle.set()

        self._log_result(notification, errors)

    def _should_filter(self, notification: Notification) -> bool:
        """Return True if the notification is below min_severity."""
        if _SEVERITY_ORDER[notification.severity] < _SEVERITY_ORDER[self._min_severity]:
            logger.debug(
                NOTIFICATION_FILTERED,
                notification_id=notification.id,
                severity=notification.severity,
                min_severity=self._min_severity,
            )
            return True
        return False

    def _log_result(
        self,
        notification: Notification,
        errors: list[str | None],
    ) -> None:
        """Log dispatch outcome after TaskGroup completes."""
        failed = sum(1 for e in errors if e is not None)
        if failed:
            logger.warning(
                NOTIFICATION_DISPATCH_FAILED,
                notification_id=notification.id,
                category=notification.category,
                total_sinks=len(self._sinks),
                failed=failed,
            )
        else:
            logger.debug(
                NOTIFICATION_DISPATCHED,
                notification_id=notification.id,
                category=notification.category,
                sinks=len(errors),
            )

    def _log_exception_group(
        self,
        notification: Notification,
        errors: list[str | None],
        eg: ExceptionGroup,
    ) -> None:
        """Log ExceptionGroup with per-sink context preserved."""
        partial_errors = [e for e in errors if e is not None]
        error_types = sorted({type(e).__name__ for e in eg.exceptions})
        logger.warning(
            NOTIFICATION_DISPATCH_FAILED,
            notification_id=notification.id,
            category=notification.category,
            error_types=error_types,
            partial_sink_errors=partial_errors,
        )

    @staticmethod
    async def _guarded_send(
        sink: NotificationSink,
        notification: Notification,
        errors: list[str | None],
        index: int,
    ) -> None:
        """Send to a single sink, capturing errors."""
        try:
            await sink.send(notification)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            description = safe_error_description(exc)
            errors[index] = description
            logger.warning(
                NOTIFICATION_DISPATCH_FAILED,
                notification_id=notification.id,
                sink_name=sink.sink_name,
                error_type=type(exc).__name__,
                error=description,
            )
