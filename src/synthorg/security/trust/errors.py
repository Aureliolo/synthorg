"""Trust domain error hierarchy."""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class TrustError(DomainError):
    """Base error for all trust operations."""

    default_message: ClassVar[str] = "Trust operation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class TrustEvaluationError(TrustError):
    """Error during trust evaluation."""
