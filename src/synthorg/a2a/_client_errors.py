"""Outbound A2A client error hierarchy.

Lives in its own leaf module so the skill-negotiation mixin can raise
:class:`A2AClientError` without importing the concrete client, which
would form an import cycle.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class A2AClientError(DomainError):
    """Error raised by the outbound A2A client.

    Non-retryable by default. Transient peer failures (HTTP 429,
    connection resets, timeouts) raise :class:`A2ATransientError`, which
    the project-standard ``is_retryable`` class attribute marks for retry.
    """

    default_message: ClassVar[str] = "A2A client request failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.PROVIDER_ERROR
    error_code: ClassVar[ErrorCode] = ErrorCode.PROVIDER_ERROR
    status_code: ClassVar[int] = 502
    is_retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        peer_name: str = "",
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.peer_name = peer_name
        # Callers raising the transient subclass for a 429 pass the peer's
        # advertised cool-off here so a retry layer can honour it.
        self.retry_after_seconds = retry_after_seconds


class A2ATransientError(A2AClientError):
    """Retryable A2A failure: 429 back-pressure or a connect / timeout error.

    Keeps the parent's ``PROVIDER_ERROR`` code (inheritance alias) and
    flips both retryability flags so either the ``is_retryable`` ClassVar
    or the ``DomainError.retryable`` accessor marks it for retry.
    """

    is_retryable: ClassVar[bool] = True
    retryable: ClassVar[bool] = True
