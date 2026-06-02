# module-kind: declarative
"""Domain errors for the deliverable-receipts feature.

Every error subclasses :class:`synthorg.core.domain_errors.DomainError`
with an :class:`ErrorCode` whose first digit matches the declared
:class:`ErrorCategory`. The base ``DomainError.__init_subclass__``
enforces the prefix-versus-category invariant at class-definition time,
so callers can catch the whole family via :class:`DeliverableReceiptError`.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class DeliverableReceiptError(DomainError):
    """Base exception for all deliverable-receipt operations.

    Subclasses keep the inherited ``ErrorCode.INTERNAL_ERROR`` default
    unless they declare a more specific code below.
    """


class DeliverableReceiptNotFoundError(DeliverableReceiptError):
    """Raised when no receipt exists for the requested deliverable."""

    default_message: ClassVar[str] = "Deliverable receipt not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.DELIVERABLE_RECEIPT_NOT_FOUND
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 404


class DeliverableReceiptBuildError(DeliverableReceiptError):
    """Raised when assembling a receipt from its signal substrate fails.

    Retryable: a transient persistence or workspace fault may succeed on
    a later rebuild. The receipt build seam catches this (and any other
    exception) so a build failure never rolls back the deliverable's
    COMPLETED transition.
    """

    default_message: ClassVar[str] = "Deliverable receipt build failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.DELIVERABLE_RECEIPT_BUILD_ERROR
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 500
