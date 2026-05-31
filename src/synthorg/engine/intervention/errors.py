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


class SteeringDirectiveFieldError(DomainError):
    """Raised when a steering directive is issued with a blank required field.

    ``project_id``, ``text`` and ``author`` are typed ``NotBlankStr``, but that
    alias is an ``Annotated[str, ...]`` whose constraints only run during Pydantic
    model validation -- a direct service call can still pass a blank string. The
    single brain-write path guards explicitly so a directive with an empty title,
    summary, or audit author never reaches persistence.
    """

    default_message: ClassVar[str] = "Steering directive field must not be blank"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.STEERING_DIRECTIVE_FIELD_BLANK
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 422


class SteeringTaskProjectMismatchError(DomainError):
    """Raised when an explicit supersession targets a task from another project.

    ``cancel_task`` cancels by raw id without a project check, so an out-of-scope
    id would cancel another project's task while the steering event still claims
    the directive's project. The supersede path validates ownership against the
    directive's project before cancelling anything.
    """

    default_message: ClassVar[str] = "Supersede task does not belong to the project"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.STEERING_TASK_PROJECT_MISMATCH
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 422
