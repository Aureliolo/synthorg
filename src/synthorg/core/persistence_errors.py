"""Persistence error hierarchy.

All persistence-related errors inherit from :class:`PersistenceError` so
callers can catch the entire family with a single except clause.
``PersistenceError`` itself is rooted in :class:`DomainError` so the API
layer's centralised RFC 9457 dispatch picks up every subtype; the
existing ``handle_record_not_found`` / ``handle_persistence_integrity_error``
/ ``handle_duplicate_record`` / ``handle_persistence_error`` handlers
remain registered so persistence-layer 4xx responses keep their fixed
public messages (``"Resource not found"``, etc.) instead of leaking
record identifiers from ``str(exc)``.

Each concrete exception carries an ``is_retryable`` class attribute
mirroring the provider-layer convention in
:mod:`synthorg.providers.errors`. Callers that implement bounded
retry/backoff (e.g. a repository middleware) can branch on this flag
without string-matching the driver exception. Default: ``False``.
Transient I/O failures override to ``True``.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class PersistenceError(DomainError):
    """Base exception for all persistence operations."""

    is_retryable: bool = False
    default_message: ClassVar[str] = "Persistence operation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.PERSISTENCE_ERROR
    status_code: ClassVar[int] = 500


class PersistenceConnectionError(PersistenceError):
    """Raised when a backend connection cannot be established or is lost.

    Network drops, pool exhaustion, and connect timeouts are transient
    by default -- callers can retry with backoff.
    """

    is_retryable: bool = True


class MigrationError(PersistenceError):
    """Raised when a database migration fails.

    Non-retryable: a failed migration indicates schema drift or a
    logic bug, not a transient condition.
    """

    is_retryable: bool = False


class RecordNotFoundError(PersistenceError):
    """Raised when a requested record does not exist.

    Used by ``ArtifactStorageBackend.retrieve()`` when no content
    exists for the given artifact ID. Repository ``get()`` methods
    return ``None`` on miss instead of raising.
    """

    is_retryable: bool = False
    default_message: ClassVar[str] = "Resource not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.RECORD_NOT_FOUND
    status_code: ClassVar[int] = 404


class DuplicateRecordError(PersistenceError):
    """Raised when inserting a record that already exists."""

    is_retryable: bool = False
    default_message: ClassVar[str] = "Duplicate record"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.DUPLICATE_RECORD
    status_code: ClassVar[int] = 409


class QueryError(PersistenceError):
    """Raised when a query fails due to invalid parameters or backend issues.

    Transient by default: connection drops and deadlocks during a
    query surface here and are safe to retry. Deterministic failures
    (bad SQL, invalid params) use :class:`ConstraintViolationError`
    or :class:`PersistenceVersionConflictError` which override to
    non-retryable.
    """

    is_retryable: bool = True


class ConstraintViolationError(QueryError):
    """Raised when a DB constraint (unique, check, trigger) is violated.

    Carries a ``constraint`` attribute that identifies the violated
    constraint by its DB-side name (for Postgres) or by a stable
    token parsed from the error message (for SQLite). Callers can
    check this attribute to map the violation to a domain error
    without parsing error strings.

    Non-retryable: constraint violations are deterministic for a
    given input and will not succeed on a bare retry.

    Blank ``constraint`` (empty / whitespace-only) is normalised to the
    sentinel ``"<unknown>"`` rather than raising. Raising
    :class:`ValueError` from ``__init__`` would bypass downstream
    ``except PersistenceError`` handlers; the sentinel keeps the
    construction inside the persistence-error family so callers that
    branch on ``constraint`` see a known token they can detect.
    """

    UNKNOWN_CONSTRAINT: str = "<unknown>"

    is_retryable: bool = False
    default_message: ClassVar[str] = "Database constraint violated"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    # 400 instead of 422: a DB-level constraint violation is a
    # malformed-request condition surfaced after the request reached the
    # data layer (e.g. unique-key collision under concurrent insert), not
    # a Pydantic-style schema-validation miss caught at the API boundary.
    # The dedicated ``handle_persistence_integrity_error`` handler also
    # hardcodes 400 for the same reason; this ClassVar matches that
    # mapping so the two paths stay in lockstep.
    status_code: ClassVar[int] = 400

    def __init__(self, message: str, *, constraint: str) -> None:
        super().__init__(message)
        stripped = constraint.strip()
        self.constraint: str = stripped or self.UNKNOWN_CONSTRAINT


class PersistenceVersionConflictError(
    QueryError
):  # lint-allow: error-code-uniqueness -- twin of domain VersionConflictError
    """Raised when an optimistic concurrency version check fails.

    Non-retryable at this layer: the caller must re-read, re-apply
    its intended change, and resubmit with the fresh version. A
    blind retry would just lose the racing write.

    The API layer translates this to
    :class:`synthorg.core.domain_errors.VersionConflictError` (the
    HTTP-aware sibling) so that controllers raise / catch consistently
    with other 409 paths.
    """

    is_retryable: bool = False
    default_message: ClassVar[str] = "Optimistic concurrency conflict"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.VERSION_CONFLICT
    status_code: ClassVar[int] = 409


class MalformedRowError(QueryError):
    """Raised when a persisted row cannot be deserialized into its model.

    JSON decode failures, validation errors, and missing-key errors on
    rows already committed to the database are deterministic
    data-integrity problems, not transient query failures. Retrying
    the same read returns the same corrupt row -- it just burns the
    budget and obscures the underlying integrity issue.

    Non-retryable: callers must investigate the source row, not retry.
    """

    is_retryable: bool = False


class ArtifactTooLargeError(PersistenceError):
    """Raised when a single artifact exceeds the maximum allowed size."""

    is_retryable: bool = False
    default_message: ClassVar[str] = "Artifact exceeds maximum size"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.ARTIFACT_TOO_LARGE
    status_code: ClassVar[int] = 413


class ArtifactStorageFullError(PersistenceError):
    """Raised when total artifact storage exceeds capacity."""

    is_retryable: bool = False
    default_message: ClassVar[str] = "Artifact storage at capacity"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.ARTIFACT_STORAGE_FULL
    status_code: ClassVar[int] = 507


class JsonbQueryUnsupportedError(PersistenceError):
    """Raised when a JSONB-native query hits a backend that lacks the capability.

    JSONB containment / key-existence queries rely on Postgres-native
    operators (``@>`` / ``?``) that SQLite does not provide. Backends
    without the capability raise this typed error so the API surfaces a
    stable 422 instead of the controller branching on an
    ``isinstance(repo, JsonbQueryCapability)`` capability probe.

    Non-retryable: the backend will not gain the capability on a retry.
    """

    is_retryable: bool = False
    default_message: ClassVar[str] = "JSONB queries require the Postgres backend"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    status_code: ClassVar[int] = 422
