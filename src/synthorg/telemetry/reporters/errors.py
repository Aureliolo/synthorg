"""Telemetry reporter exception types.

Precise exception classes let the reporter factory distinguish the
three legitimate init-failure modes -- logfire not installed, build
artifact missing the embedded token, SDK configure failure -- and
log the actual class name instead of swallowing every failure as
``ImportError``. Anything outside these three classes propagates so
silent fallback to ``NoopReporter`` never hides a programming bug.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class LogfireTokenMissingError(DomainError):
    """Raised when the build artifact ships the sentinel token."""

    default_message: ClassVar[str] = "Logfire token missing"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class LogfireConfigureError(DomainError):
    """Raised when ``logfire.configure()`` fails at init time."""

    default_message: ClassVar[str] = "Logfire configure failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500
