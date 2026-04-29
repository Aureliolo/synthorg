"""Telemetry reporter exception types.

Precise exception classes let the reporter factory distinguish the
three legitimate init-failure modes -- logfire not installed, build
artifact missing the embedded token, SDK configure failure -- and
log the actual class name instead of swallowing every failure as
``ImportError``. Anything outside these three classes propagates so
silent fallback to ``NoopReporter`` never hides a programming bug.
"""


class LogfireTokenMissingError(RuntimeError):
    """Raised when the build artifact ships the sentinel token."""


class LogfireConfigureError(RuntimeError):
    """Raised when ``logfire.configure()`` fails at init time."""
