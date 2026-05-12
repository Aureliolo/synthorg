"""Protocols for the telemetry event counter.

The counter is a read-side observer of the telemetry pipeline: it
subscribes to every :class:`TelemetryEvent` the collector accepts
(post privacy scrub) and maintains rolling counts by event type.  The
telemetry signal aggregator queries it per observation window.

Design:
- Separate from :class:`TelemetryCollector` (which remains write-only
  toward the reporter backend) so adding a counter does not leak the
  reporter responsibility into a new class.
- :class:`TelemetrySubscriber` is the minimal sink-style protocol the
  collector fans out to after scrubber validation.  Any number of
  subscribers can be registered; the counter is one of them.
- :class:`TelemetryEventCounter` exposes read methods over the
  accumulated counts without requiring knowledge of the subscriber
  plumbing, so tests can feed events directly into the counter.
"""

from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

_DEFAULT_MAX_TOP: Final[int] = 10

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.meta.signal_models import OrgTelemetrySummary
    from synthorg.telemetry.protocol import TelemetryEvent


@runtime_checkable
class TelemetrySubscriber(Protocol):
    """Callback invoked by :class:`TelemetryCollector` for each valid event.

    Subscribers must be best-effort: log and swallow their own errors
    (except ``MemoryError`` / ``RecursionError``) so the telemetry
    pipeline is never blocked by subscriber failures.

    The method is synchronous on purpose: subscribers run on the
    collector's ``_send`` hot path and must not await external I/O.
    The counter's implementation satisfies this by mutating an
    in-memory deque under a lightweight lock.
    """

    def on_event(self, event: TelemetryEvent) -> None:
        """Receive a scrubbed telemetry event."""
        ...


@runtime_checkable
class TelemetryEventCounter(Protocol):
    """Rolling event-count store that feeds the telemetry aggregator."""

    def on_event(self, event: TelemetryEvent) -> None:
        """Record a telemetry event in the counter's window."""
        ...

    async def summarize(
        self,
        *,
        since: datetime,
        until: datetime,
        max_top: int = _DEFAULT_MAX_TOP,
    ) -> OrgTelemetrySummary:
        """Produce the org-wide telemetry summary for the window.

        Args:
            since: Start of the observation window (UTC).
            until: End of the observation window (UTC).
            max_top: Cap on how many event-type names to surface in
                ``top_event_types``.

        Returns:
            Populated :class:`OrgTelemetrySummary`; empty when the
            window contains no events.
        """
        ...

    async def count(self) -> int:
        """Return the current number of events stored."""
        ...

    async def clear(self) -> None:
        """Drop all stored events.  Intended for test isolation."""
        ...
