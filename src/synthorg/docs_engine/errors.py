"""Domain errors for the living-documentation engine.

Every error here subclasses :class:`synthorg.core.domain_errors.DomainError`
with an :class:`ErrorCode` whose first digit matches the declared
:class:`ErrorCategory`. The base ``DomainError.__init_subclass__`` enforces
the prefix-versus-category invariant at class-definition time.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class DocNotFoundError(DomainError):
    """Raised when a living document or version cannot be located."""

    default_message: ClassVar[str] = "Living document not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.LIVING_DOC_NOT_FOUND
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 404


class DocVersionConflictError(DomainError):
    """Raised when a doc write collides with an existing slug or version."""

    default_message: ClassVar[str] = "Living document version conflict"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.LIVING_DOC_VERSION_CONFLICT
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 409


class DocValidationError(DomainError):
    """Raised when a doc payload fails structural validation."""

    default_message: ClassVar[str] = "Living document validation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.LIVING_DOC_VALIDATION_ERROR
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 422


class DocIndexError(DomainError):
    """Raised when chunking or RAG indexing of a doc fails.

    The on-disk commit may have succeeded even when this fires, so
    callers reading ``last_indexed_commit_sha`` should treat the gap
    between ``head_commit_sha`` and ``last_indexed_commit_sha`` as
    unindexed work eligible for replay.
    """

    default_message: ClassVar[str] = "Living document indexing failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.LIVING_DOC_INDEX_ERROR
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 500


class DocCommitError(DomainError):
    """Raised when serialising or committing a doc to the workspace fails.

    Distinct from :class:`DocIndexError`: a commit failure means nothing
    landed on disk (or push), so the read path remains consistent.
    """

    default_message: ClassVar[str] = "Living document commit failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.LIVING_DOC_COMMIT_ERROR
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 500
