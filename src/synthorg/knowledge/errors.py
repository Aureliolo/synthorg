"""Domain errors for the knowledge substrate.

Every error subclasses :class:`synthorg.core.domain_errors.DomainError`
with an :class:`ErrorCode` whose first digit matches the declared
:class:`ErrorCategory`. The base ``DomainError.__init_subclass__``
enforces the prefix-versus-category invariant at class-definition time,
so callers can catch the whole family via :class:`KnowledgeError`.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class KnowledgeError(DomainError):
    """Base exception for all knowledge-substrate operations.

    Subclasses keep the inherited ``ErrorCode.INTERNAL_ERROR`` default
    unless they declare a more specific code below.
    """


class KnowledgeSourceNotFoundError(KnowledgeError):
    """Raised when a knowledge source cannot be located."""

    default_message: ClassVar[str] = "Knowledge source not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.KNOWLEDGE_SOURCE_NOT_FOUND
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 404


class KnowledgeValidationError(KnowledgeError):
    """Raised when an ingestion payload fails structural validation."""

    default_message: ClassVar[str] = "Knowledge payload validation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.KNOWLEDGE_VALIDATION_ERROR
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 422


class KnowledgeIngestError(KnowledgeError):
    """Raised when loading, chunking, or indexing a source fails.

    Retryable: a transient backend or fetch failure may succeed on a
    later re-ingest. The source row is marked ``FAILED`` with a safe
    error description so an operator can retry.
    """

    default_message: ClassVar[str] = "Knowledge ingestion failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.KNOWLEDGE_INGEST_ERROR
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 500


class KnowledgeRetrievalError(KnowledgeError):
    """Raised when a knowledge search or citation resolution fails."""

    default_message: ClassVar[str] = "Knowledge retrieval failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.KNOWLEDGE_RETRIEVAL_ERROR
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 500


class KnowledgeDependencyError(KnowledgeError):
    """Raised when an optional ingestion dependency is not installed.

    The ``pdfplumber`` / ``tree-sitter`` / ``tree-sitter-language-pack``
    packages ship in the optional ``synthorg[knowledge]`` extra so the
    base install stays lean; this error indicates the extra is absent.
    """

    default_message: ClassVar[str] = "Knowledge ingestion dependency missing"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.KNOWLEDGE_DEPENDENCY_ERROR
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 500


class KnowledgeSourceUnavailableError(KnowledgeError):
    """Raised when a source cannot be fetched (e.g. no governed connection).

    Distinct from :class:`KnowledgeIngestError`: the source itself is
    unreachable rather than the pipeline failing, so the caller should
    provision access (a connection, credentials) before retrying.
    """

    default_message: ClassVar[str] = "Knowledge source unavailable"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.KNOWLEDGE_SOURCE_UNAVAILABLE
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 503
