"""Persistence error hierarchy.

All persistence-related errors inherit from :class:`PersistenceError` so
callers can catch the entire family with a single except clause.

Each concrete exception carries an ``is_retryable`` class attribute
mirroring the provider-layer convention in
:mod:`synthorg.providers.errors`.  Callers that implement bounded
retry/backoff (e.g. a repository middleware) can branch on this flag
without string-matching the driver exception.  Default: ``False``.
Transient I/O failures override to ``True``.

This module is dependency-free apart from stdlib so the persistence
implementation (SQLite/Postgres repositories) and any consumer
(controllers, services, engine) can import it without pulling in the
HTTP layer.
"""


class PersistenceError(Exception):
    """Base exception for all persistence operations."""

    is_retryable: bool = False


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
    exists for the given artifact ID.  Repository ``get()`` methods
    return ``None`` on miss instead of raising.
    """

    is_retryable: bool = False


class DuplicateRecordError(PersistenceError):
    """Raised when inserting a record that already exists."""

    is_retryable: bool = False


class QueryError(PersistenceError):
    """Raised when a query fails due to invalid parameters or backend issues.

    Transient by default: connection drops and deadlocks during a
    query surface here and are safe to retry.  Deterministic failures
    (bad SQL, invalid params) use :class:`ConstraintViolationError`
    or :class:`VersionConflictError` which override to non-retryable.
    """

    is_retryable: bool = True


class ConstraintViolationError(QueryError):
    """Raised when a DB constraint (unique, check, trigger) is violated.

    Carries a ``constraint`` attribute that identifies the violated
    constraint by its DB-side name (for Postgres) or by a stable
    token parsed from the error message (for SQLite).  Callers can
    check this attribute to map the violation to a domain error
    without parsing error strings.

    Non-retryable: constraint violations are deterministic for a
    given input and will not succeed on a bare retry.
    """

    is_retryable: bool = False

    def __init__(self, message: str, *, constraint: str) -> None:
        super().__init__(message)
        self.constraint: str = constraint


class VersionConflictError(QueryError):
    """Raised when an optimistic concurrency version check fails.

    Non-retryable at this layer: the caller must re-read, re-apply
    its intended change, and resubmit with the fresh version.  A
    blind retry would just lose the racing write.
    """

    is_retryable: bool = False


class MalformedRowError(QueryError):
    """Raised when a persisted row cannot be deserialized into its model.

    JSON decode failures, validation errors, and missing-key errors on
    rows already committed to the database are deterministic
    data-integrity problems, not transient query failures.  Retrying
    the same read returns the same corrupt row -- it just burns the
    budget and obscures the underlying integrity issue.

    Non-retryable: callers must investigate the source row, not retry.
    """

    is_retryable: bool = False


class ArtifactTooLargeError(PersistenceError):
    """Raised when a single artifact exceeds the maximum allowed size."""

    is_retryable: bool = False


class ArtifactStorageFullError(PersistenceError):
    """Raised when total artifact storage exceeds capacity."""

    is_retryable: bool = False
