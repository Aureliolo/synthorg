"""Domain errors for the research subsystem.

Every error subclasses :class:`synthorg.core.domain_errors.DomainError`
with an :class:`ErrorCode` whose first digit matches the declared
:class:`ErrorCategory`. The base ``DomainError.__init_subclass__``
enforces the prefix-versus-category invariant at class-definition time,
so callers can catch the whole family via :class:`ResearchError`.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class ResearchError(DomainError):
    """Base exception for all research-subsystem operations.

    Subclasses keep the inherited ``ErrorCode.INTERNAL_ERROR`` default
    unless they declare a more specific code below.
    """


class ResearchValidationError(ResearchError):
    """Raised when a research brief or run payload fails validation."""

    default_message: ClassVar[str] = "Research payload validation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.RESEARCH_VALIDATION_ERROR
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 422


class ResearchRunNotFoundError(ResearchError):
    """Raised when a research run cannot be located."""

    default_message: ClassVar[str] = "Research run not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.RESEARCH_RUN_NOT_FOUND
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 404


class ResearchRunError(ResearchError):
    """Raised when a research run fails for a reason without a finer code.

    Retryable: a transient downstream failure may succeed on a fresh run.
    """

    default_message: ClassVar[str] = "Research run failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.RESEARCH_RUN_ERROR
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 500


class ResearchRetrievalError(ResearchError):
    """Raised when a retrieval source fails to return candidates.

    Retryable: a transient provider or network failure may succeed later.
    """

    default_message: ClassVar[str] = "Research retrieval failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.RESEARCH_RETRIEVAL_ERROR
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 500


class ResearchSynthesisError(ResearchError):
    """Raised when synthesis produces an invalid or unsourced report.

    Not retryable: the synthesiser violated the citation invariant (a
    claim cited an unknown source or no source at all), so the run must be
    inspected rather than blindly retried.
    """

    default_message: ClassVar[str] = "Research synthesis failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.RESEARCH_SYNTHESIS_ERROR
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 500


class ResearchBudgetExceededError(ResearchError):
    """Raised when a run breaches its declared cost or wall-clock ceiling.

    Not retryable: the same brief under the same ceilings would breach
    again, so the run must be re-scoped (raise the ceiling or narrow the
    question) rather than blindly retried.
    """

    default_message: ClassVar[str] = "Research run exceeded its budget"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.BUDGET_EXHAUSTED
    error_code: ClassVar[ErrorCode] = ErrorCode.RESEARCH_BUDGET_EXCEEDED
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 429


class ResearchUnavailableError(ResearchError):
    """Raised when research mode is not wired (disabled or missing deps)."""

    default_message: ClassVar[str] = "Research mode unavailable"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.RESEARCH_UNAVAILABLE
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 503
