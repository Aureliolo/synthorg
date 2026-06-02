# module-kind: code
"""Domain errors for the run-narrative engine.

Both errors subclass :class:`synthorg.core.domain_errors.DomainError` with
an :class:`ErrorCode` whose first digit matches the declared
:class:`ErrorCategory`; the base ``__init_subclass__`` enforces the
prefix-versus-category invariant at class-definition time.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class NarrativeSourceUnavailableError(DomainError):
    """Raised when a run has no flight-recorder frames to narrate.

    A brief that produced no recorded turns has nothing to chronicle, so
    the narrator skips generation rather than writing an empty doc. The
    pipeline trigger treats this as a benign skip.
    """

    default_message: ClassVar[str] = "No run activity available to narrate"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.NARRATIVE_SOURCE_UNAVAILABLE
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 500


class NarrativeGenerationError(DomainError):
    """Raised when assembling or persisting the narrative fails.

    Distinct from :class:`NarrativeSourceUnavailableError`: the sources
    were present but the narrative could not be produced or written. The
    pipeline trigger logs and degrades; it never fails the run.
    """

    default_message: ClassVar[str] = "Run narrative generation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.NARRATIVE_GENERATION_ERROR
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 500
