"""No-op telemetry reporter.

Used when telemetry is disabled (the default).  All methods are
no-ops with zero overhead -- no network calls, no buffering, no
background threads.
"""

from synthorg.telemetry.protocol import TelemetryEvent


class NoopReporter:
    """Reporter that silently discards all events."""

    async def report(self, event: TelemetryEvent) -> None:
        """Discard the event."""

    async def flush(self) -> None:
        """No-op flush."""

    async def shutdown(self) -> None:
        """No-op shutdown."""
