"""In-memory telemetry event counter.

Process-local rolling buffer of :class:`TelemetryEvent` timestamps +
types.  Implements the
:class:`~synthorg.telemetry.event_counter_protocol.TelemetryEventCounter`
protocol (read surface) and the
:class:`~synthorg.telemetry.event_counter_protocol.TelemetrySubscriber`
protocol (write surface) so it can be registered directly with the
:class:`TelemetryCollector` via ``subscribe()``.

The counter is the single owner of event-count roll-up logic; the
telemetry signal aggregator calls :meth:`summarize` rather than
reimplementing windowed counts and top-type ranking.
"""

import threading
from collections import deque
from datetime import datetime
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.signal_models import OrgTelemetrySummary
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.telemetry import (
    TELEMETRY_COUNTER_EVICTED,
    TELEMETRY_COUNTER_RECORD_FAILED,
)
from synthorg.telemetry.protocol import TelemetryEvent

logger = get_logger(__name__)
_DEFAULT_MAX_TOP: Final[int] = 10

_DEFAULT_MAX_EVENTS: Final[int] = 10_000
"""Default ring-buffer capacity for telemetry events.

Telemetry events fire on deployment lifecycle boundaries + heartbeat;
this covers many weeks of normal traffic.  Durable backends behind
the protocol are the right path for multi-month retention.
"""

_ERROR_EVENT_NAME_HINTS: tuple[str, ...] = (
    ".failed",
    ".error",
    ".denied",
    ".rejected",
)
"""Substring hints used to classify an event type as error-bearing.

Telemetry events carry their severity via the event type name
(``deployment.startup`` vs. ``deployment.report.failed``).  The hint
list lets the counter surface ``error_event_count`` without a
separate enum.  New hints are additive: appending one does not
invalidate existing counts because the whole event buffer is
re-scanned at summarise time.
"""


class InMemoryTelemetryEventCounter:
    """Process-local rolling telemetry-event counter.

    Args:
        max_events: Ring-buffer capacity.  Oldest entries are evicted
            when the buffer is full.
    """

    def __init__(self, *, max_events: int = _DEFAULT_MAX_EVENTS) -> None:
        if max_events < 1:
            msg = f"max_events must be >= 1, got {max_events}"
            raise ValueError(msg)
        self._max_events = max_events
        # Store (timestamp, event_type) tuples to minimise memory.
        self._events: deque[tuple[datetime, str]] = deque(maxlen=max_events)
        # Threading lock because ``on_event`` is synchronous and may
        # be called from any thread the reporter dispatches from.
        self._lock = threading.Lock()
        # One-shot flag: the buffer is full and we've already logged it
        # once; further appends keep evicting but we don't re-log on
        # every call (that would be a log storm at max capacity).
        self._eviction_logged = False

    def on_event(self, event: TelemetryEvent) -> None:
        """Record one telemetry event.

        Synchronous, best-effort; swallows all exceptions except
        ``MemoryError`` / ``RecursionError``.
        """
        try:
            with self._lock:
                first_eviction = (
                    len(self._events) == self._max_events and not self._eviction_logged
                )
                self._events.append((event.timestamp, event.event_type))
                if first_eviction:
                    self._eviction_logged = True
            if first_eviction:
                logger.info(
                    TELEMETRY_COUNTER_EVICTED,
                    max_events=self._max_events,
                )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TELEMETRY_COUNTER_RECORD_FAILED,
                event_type=getattr(event, "event_type", "<unknown>"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def summarize(
        self,
        *,
        since: datetime,
        until: datetime,
        max_top: int = _DEFAULT_MAX_TOP,
    ) -> OrgTelemetrySummary:
        """Roll recorded events into an :class:`OrgTelemetrySummary`.

        Returns:
            A summary of events in ``[since, until)``: total count, the
            top event types (up to ``max_top``), and the error-event
            count. An empty summary when no events fall in the window.

        Raises:
            ValueError: When the window is naive or inverted, or
                ``max_top`` is non-positive.
        """
        _validate_window(since, until)
        if max_top < 1:
            msg = f"max_top must be >= 1, got {max_top}"
            raise ValueError(msg)
        with self._lock:
            snapshot = tuple(self._events)
        in_window = [(ts, et) for ts, et in snapshot if since <= ts < until]
        if not in_window:
            return OrgTelemetrySummary()
        type_counts: dict[str, int] = {}
        error_count = 0
        for _ts, event_type in in_window:
            type_counts[event_type] = type_counts.get(event_type, 0) + 1
            if _is_error_event(event_type):
                error_count += 1
        top_types = _rank_top_types(type_counts, max_top=max_top)
        return OrgTelemetrySummary(
            event_count=len(in_window),
            top_event_types=top_types,
            error_event_count=error_count,
        )

    async def count(self) -> int:
        """Return current buffer size (not capacity)."""
        with self._lock:
            return len(self._events)

    async def clear(self) -> None:
        """Drop all stored events and reset the eviction log sentinel."""
        with self._lock:
            self._events.clear()
            # Reset the one-shot eviction flag so a future saturation
            # after this clear re-emits TELEMETRY_COUNTER_EVICTED; left
            # set it would mask re-saturation as silent.
            self._eviction_logged = False


def _validate_window(since: datetime, until: datetime) -> None:
    """Reject inverted or naive windows before any scan.

    Raises:
        ValueError: When either bound is naive (no tzinfo) or ``since``
            is not strictly earlier than ``until``.
    """
    if since.tzinfo is None or until.tzinfo is None:
        msg = "since/until must be timezone-aware"
        raise ValueError(msg)
    if since >= until:
        msg = (
            f"since ({since.isoformat()}) must be earlier than until "
            f"({until.isoformat()})"
        )
        raise ValueError(msg)


def _is_error_event(event_type: str) -> bool:
    """Return ``True`` when the type name matches an error hint.

    Hints are compared case-insensitively; the event type namespace
    is mixed case in the wild (``TELEMETRY_REPORT_FAILED`` emits
    ``telemetry.report.failed``) and we want the match to be stable.
    """
    lower = event_type.lower()
    return any(hint in lower for hint in _ERROR_EVENT_NAME_HINTS)


def _rank_top_types(
    type_counts: dict[str, int],
    *,
    max_top: int,
) -> tuple[str, ...]:
    """Return the top event-type names by count, alphabetical tie-break."""
    ranked = sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return tuple(name for name, _ in ranked[:max_top])


__all__ = [
    "InMemoryTelemetryEventCounter",
]
