"""Domain errors for mid-flight steering.

Every error subclasses :class:`synthorg.core.domain_errors.DomainError` with an
:class:`ErrorCode` whose first digit matches the declared
:class:`ErrorCategory`; the base ``DomainError.__init_subclass__`` enforces that
invariant at class-definition time.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class SteeringKindError(DomainError):
    """Raised when a steering directive is issued with a non-steerable kind.

    Only ``HINT`` and ``REDIRECT`` propagate into running agents; ``PAUSE`` and
    ``KILL`` are task-lifecycle interventions handled elsewhere. Rejecting them
    here keeps the brain free of steering entries the inbox would silently drop.
    """

    default_message: ClassVar[str] = "Steering directive kind is not steerable"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.STEERING_KIND_INVALID
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 422
