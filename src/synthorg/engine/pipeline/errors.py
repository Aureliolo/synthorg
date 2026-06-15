"""Work pipeline domain errors.

All inherit from :class:`WorkPipelineError`, itself a
:class:`DomainError`, so the existing
``(DomainError, handle_domain_error)`` registration in
:mod:`synthorg.api.exception_handlers` dispatches every subclass via
MRO; no bespoke handler is required.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class WorkPipelineError(DomainError):
    """Base for every work pipeline failure (500 unless overridden)."""

    default_message: ClassVar[str] = "Work pipeline failure"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class WorkIntakeRejectedError(WorkPipelineError):
    """Raised when the intake strategy rejects the submitted work (422)."""

    default_message: ClassVar[str] = "Work was rejected at intake"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    status_code: ClassVar[int] = 422


class WorkRoutingUndecidableError(WorkPipelineError):
    """Raised when the spine cannot route the work to an executor (500).

    Covers an unknown routing-policy discriminator, an empty active
    agent pool, and no agent scoring above the routing threshold for
    the solo (leaf) path.
    """

    default_message: ClassVar[str] = "Work could not be routed to an executor"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class WorkPipelineTeamPathUnavailableError(WorkPipelineError):
    """Raised when splittable work needs the coordinator but it is absent.

    The empty-company (no-provider) boot leaves the coordinator
    unconfigured; routing a ``SPLITTABLE`` verdict then honestly 503s
    rather than silently degrading to a single agent.
    """

    default_message: ClassVar[str] = (
        "Multi-agent coordinator is not configured; "
        "configure a provider to run splittable work"
    )
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.SERVICE_UNAVAILABLE
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 503
