"""Downstream classification sinks.

Implements ``ClassificationSink`` for wiring classification
results into the performance tracker and notification dispatcher.
"""

import asyncio
import copy
import time
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.budget.coordination_config import ErrorCategory
from synthorg.engine.classification.models import ErrorSeverity
from synthorg.hr.performance.models import CollaborationMetricRecord
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.classification import (
    CLASSIFICATION_SINK_ERROR,
    NOTIFICATION_RATE_LIMITED,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.engine.classification.models import ClassificationResult
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.notifications.dispatcher import NotificationDispatcher

logger = get_logger(__name__)

_SEVERITY_MAP: MappingProxyType[ErrorSeverity, NotificationSeverity] = MappingProxyType(
    copy.deepcopy(
        {
            ErrorSeverity.HIGH: NotificationSeverity.ERROR,
            ErrorSeverity.MEDIUM: NotificationSeverity.WARNING,
            ErrorSeverity.LOW: NotificationSeverity.INFO,
        },
    ),
)

_CATEGORY_MAP: MappingProxyType[ErrorCategory, NotificationCategory] = MappingProxyType(
    copy.deepcopy(
        {
            ErrorCategory.LOGICAL_CONTRADICTION: NotificationCategory.AGENT,
            ErrorCategory.NUMERICAL_DRIFT: NotificationCategory.AGENT,
            ErrorCategory.CONTEXT_OMISSION: NotificationCategory.AGENT,
            ErrorCategory.COORDINATION_FAILURE: NotificationCategory.SYSTEM,
            ErrorCategory.DELEGATION_PROTOCOL_VIOLATION: (
                NotificationCategory.SECURITY
            ),
            ErrorCategory.REVIEW_PIPELINE_VIOLATION: (NotificationCategory.SECURITY),
            ErrorCategory.AUTHORITY_BREACH_ATTEMPT: (NotificationCategory.SECURITY),
        },
    ),
)

_SEVERITY_ORDER: MappingProxyType[ErrorSeverity, int] = MappingProxyType(
    copy.deepcopy(
        {
            ErrorSeverity.LOW: 0,
            ErrorSeverity.MEDIUM: 1,
            ErrorSeverity.HIGH: 2,
        },
    ),
)


class PerformanceTrackerSink:
    """Forwards classification findings to the performance tracker.

    Records each finding as a collaboration event for the agent,
    enabling trend detection and evolution triggers.

    Args:
        tracker: The performance tracker to record events to.
    """

    def __init__(self, tracker: PerformanceTracker) -> None:
        self._tracker = tracker

    async def on_classification(
        self,
        result: ClassificationResult,
    ) -> None:
        """Record classification findings as collaboration events.

        Best-effort: logs errors internally, never raises
        (except MemoryError/RecursionError).

        Args:
            result: The completed classification result.
        """
        if not result.has_findings:
            return

        for finding in result.findings:
            try:
                record = CollaborationMetricRecord(
                    agent_id=result.agent_id,
                    recorded_at=datetime.now(UTC),
                    interaction_summary=(
                        f"[{finding.category.value}] {finding.description}"
                    ),
                )
                await self._tracker.record_collaboration_event(record)
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                # Never use logger.exception here -- the traceback
                # can leak sensitive locals. Use the safe-warning
                # shape that the dispatcher sink already follows.
                logger.warning(
                    CLASSIFICATION_SINK_ERROR,
                    agent_id=result.agent_id,
                    task_id=result.task_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )


class _SlidingWindowRateLimiter:
    """Per-key sliding-window rate limiter.

    Tracks the timestamps of recent ``take`` calls per key and
    admits a new call only when the number of timestamps inside
    the configured window is strictly less than ``max_events``.
    Older timestamps are pruned on every call.

    The implementation is intentionally simple and in-process: it
    is per-sink state and does not share information across
    processes or tasks beyond the owning event loop.

    Args:
        max_events: Maximum admissions per sliding window.
        window_seconds: Length of the sliding window in seconds.
        clock: Optional monotonic clock, injectable for tests.
    """

    def __init__(
        self,
        *,
        max_events: int,
        window_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_events < 1:
            msg = "max_events must be >= 1"
            logger.error(
                CLASSIFICATION_SINK_ERROR,
                component="sliding_window_rate_limiter",
                max_events=max_events,
                reason=msg,
            )
            raise ValueError(msg)
        if window_seconds <= 0:
            msg = "window_seconds must be > 0"
            logger.error(
                CLASSIFICATION_SINK_ERROR,
                component="sliding_window_rate_limiter",
                window_seconds=window_seconds,
                reason=msg,
            )
            raise ValueError(msg)
        self._max_events = max_events
        self._window_seconds = window_seconds
        self._clock: Callable[[], float] = clock or time.monotonic
        # Each per-key entry stores ``(handle, timestamp)`` pairs. The
        # ``handle`` is an opaque ``object`` minted by ``take`` and
        # returned to the caller; ``release`` removes that exact entry
        # rather than popping the latest timestamp -- otherwise a slow
        # admission whose dispatch fails could refund a *later*
        # admission's slot, leaving the failed admission counted.
        self._events: dict[str, list[tuple[object, float]]] = {}
        self._lock = asyncio.Lock()

    async def take(self, key: str) -> object | None:
        """Attempt to consume one admission for ``key``.

        Returns an opaque admission handle when the slot was granted
        (the caller may proceed and must pass the same handle to
        ``release`` if the downstream action ultimately fails) or
        ``None`` when the window is saturated. The handle is an
        ``object()`` instance with no public attributes; callers MUST
        treat it as opaque and only use it for ``release``.

        Prunes stale entries for idle keys on each call to prevent
        unbounded growth of ``_events`` from one-off agent IDs.

        The dict reads / writes execute under ``self._lock`` so two
        concurrent ``take()`` calls cannot both observe ``len(events)
        < max_events`` and admit beyond the configured budget.
        """
        async with self._lock:
            now = self._clock()
            cutoff = now - self._window_seconds
            # Prune idle keys whose latest timestamp is outside the window.
            stale_keys = [
                k
                for k, entries in self._events.items()
                if k != key and (not entries or entries[-1][1] <= cutoff)
            ]
            for k in stale_keys:
                del self._events[k]
            entries = [
                (handle, ts) for handle, ts in self._events.get(key, []) if ts > cutoff
            ]
            if len(entries) >= self._max_events:
                self._events[key] = entries
                return None
            handle: object = object()
            entries.append((handle, now))
            self._events[key] = entries
            return handle

    async def release(self, key: str, handle: object) -> None:
        """Refund the exact admission identified by ``handle``.

        Call this when a ``take`` succeeded but the downstream action
        failed, so the slot can be reused by the next attempt. Only
        the entry whose handle is identical (``is``) to the supplied
        one is removed; if no match is found the call is a silent
        no-op (the handle expired out of the window before the failure
        was detected, which is a benign race).
        """
        async with self._lock:
            entries = self._events.get(key)
            if not entries:
                return
            remaining = [(h, ts) for h, ts in entries if h is not handle]
            if remaining:
                self._events[key] = remaining
            else:
                del self._events[key]


class NotificationDispatcherSink:
    """Forwards high-severity findings to the notification dispatcher.

    Converts ``ErrorFinding`` instances into ``Notification`` objects
    and dispatches them.  Only findings at or above ``min_severity``
    are dispatched.  Per-agent rate limiting caps the number of
    notifications that can be dispatched within a sliding window to
    protect downstream channels from alert storms (issue #228:
    "Rate limited 1/minute per agent").

    Args:
        dispatcher: The notification dispatcher.
        min_severity: Minimum severity to dispatch (default HIGH).
        max_events_per_window: Maximum notifications per agent
            within a sliding window.  Defaults to 1.
        window_seconds: Sliding window length in seconds.  Defaults
            to 60.0 (one minute).
        clock: Optional monotonic clock for deterministic tests.
    """

    def __init__(
        self,
        dispatcher: NotificationDispatcher,
        *,
        min_severity: ErrorSeverity = ErrorSeverity.HIGH,
        max_events_per_window: int = 1,
        window_seconds: float = 60.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._min_severity = min_severity
        self._min_rank = _SEVERITY_ORDER[min_severity]
        self._rate_limiter = _SlidingWindowRateLimiter(
            max_events=max_events_per_window,
            window_seconds=window_seconds,
            clock=clock,
        )

    async def on_classification(
        self,
        result: ClassificationResult,
    ) -> None:
        """Dispatch notifications for high-severity findings.

        Best-effort: logs errors internally, never raises
        (except MemoryError/RecursionError).  Notification model
        construction is wrapped in the same error boundary as the
        dispatch call so validator failures are logged instead of
        propagated.  Rate limiting skips notifications that would
        exceed the per-agent sliding-window budget and records a
        ``NOTIFICATION_RATE_LIMITED`` event.

        Args:
            result: The completed classification result.
        """
        if not result.has_findings:
            return

        for finding in result.findings:
            if _SEVERITY_ORDER[finding.severity] < self._min_rank:
                continue

            admission = await self._rate_limiter.take(result.agent_id)
            if admission is None:
                logger.info(
                    NOTIFICATION_RATE_LIMITED,
                    agent_id=result.agent_id,
                    task_id=result.task_id,
                    category=finding.category.value,
                    severity=finding.severity.value,
                )
                continue

            try:
                notification = Notification(
                    category=_CATEGORY_MAP.get(
                        finding.category,
                        NotificationCategory.SYSTEM,
                    ),
                    severity=_SEVERITY_MAP.get(
                        finding.severity,
                        NotificationSeverity.WARNING,
                    ),
                    title=f"Classification: {finding.category.value}",
                    body=finding.description,
                    source="engine.classification",
                    metadata={
                        "agent_id": result.agent_id,
                        "task_id": result.task_id,
                        "category": finding.category.value,
                        "severity": finding.severity.value,
                    },
                )
                await self._dispatcher.dispatch(notification)
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                # Best-effort path: refund the *exact* admission this
                # iteration consumed (not the newest slot for the
                # agent), and log a sanitized warning instead of
                # ``logger.exception``. ``logger.exception`` would
                # attach a traceback that can leak sensitive locals;
                # the structured warning carries the diagnostic
                # context we actually need.
                await self._rate_limiter.release(result.agent_id, admission)
                logger.warning(
                    CLASSIFICATION_SINK_ERROR,
                    agent_id=result.agent_id,
                    task_id=result.task_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
