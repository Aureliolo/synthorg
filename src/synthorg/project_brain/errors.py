"""Domain errors for the long-horizon project brain.

Every error here subclasses :class:`synthorg.core.domain_errors.DomainError`
with an :class:`ErrorCode` whose first digit matches the declared
:class:`ErrorCategory`. The base ``DomainError.__init_subclass__`` enforces the
prefix-versus-category invariant at class-definition time.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class BrainEntryNotFoundError(DomainError):
    """Raised when a brain entry or revision cannot be located."""

    default_message: ClassVar[str] = "Project brain entry not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.BRAIN_ENTRY_NOT_FOUND
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 404


class BrainEntryRevisionConflictError(DomainError):
    """Raised when an append collides on the ``(entry_id, revision)`` key."""

    default_message: ClassVar[str] = "Project brain revision conflict"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.BRAIN_ENTRY_REVISION_CONFLICT
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 409


class BrainEntryValidationError(DomainError):
    """Raised when a brain entry payload fails structural validation."""

    default_message: ClassVar[str] = "Project brain entry validation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.BRAIN_ENTRY_VALIDATION_ERROR
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 422


class BrainIndexError(DomainError):
    """Raised when chunking or RAG indexing of a brain entry fails.

    The SQL row and the on-disk commit may have succeeded even when this fires,
    so callers should treat the gap between the latest committed snapshot and
    the last-indexed snapshot as unindexed work eligible for replay.
    """

    default_message: ClassVar[str] = "Project brain indexing failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.BRAIN_INDEX_ERROR
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 500


class BrainCommitError(DomainError):
    """Raised when serialising or committing a brain snapshot fails.

    Distinct from :class:`BrainIndexError`: a commit failure means the workspace
    snapshot did not land, though the authoritative SQL row already has.
    """

    default_message: ClassVar[str] = "Project brain commit failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.BRAIN_COMMIT_ERROR
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 500
