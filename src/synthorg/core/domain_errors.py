"""Cross-layer domain exceptions with RFC 9457 metadata.

Every error class here inherits from :class:`DomainError`, which carries
the ClassVars the API exception handler reads when building an RFC 9457
response (``status_code``, ``error_code``, ``error_category``,
``retryable``, ``default_message``).  ``__init_subclass__`` validates
that the first digit of ``error_code`` matches the declared
``error_category`` so a typo cannot ship a 404 carrying an auth code.

This module is dependency-free apart from
:mod:`synthorg.core.error_taxonomy`, keeping it usable from the CLI and
any future extension that wants to raise / catch SynthOrg errors without
pulling in :mod:`synthorg.api`.
"""

from typing import ClassVar

from synthorg.core.error_taxonomy import (
    CODE_CATEGORY_PREFIX,
    NOT_FOUND_BAND,
    ErrorCategory,
    ErrorCode,
)


class DomainError(Exception):
    """Base for every RFC 9457-aware exception in the project.

    Class Attributes:
        default_message: 5xx-safe fallback used when no message is given
            and (for 5xx responses) when the handler scrubs internal
            detail before sending to the client.
        error_category: RFC 9457 high-level category.
        error_code: RFC 9457 4-digit machine-readable code; subclasses
            override.  ``__init_subclass__`` enforces that the first
            digit of this value matches ``error_category``.
        retryable: Whether the client should retry the request.
        status_code: HTTP status code for the API exception handler.
            Subclasses override; default 500 covers genuine internal
            errors that escape without explicit annotation.
    """

    default_message: ClassVar[str] = "Internal server error"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 500

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Validate that error_code's first digit matches error_category.

        Validation runs at class-definition time only.  Rebinding
        ``cls.error_code`` or ``cls.error_category`` post-definition
        (e.g. from a test fixture or dynamic patching) silently
        bypasses this check; treat the ClassVars as immutable in
        production code.  ``resource_not_found`` deliberately mutates
        the *instance* attribute, which is a different namespace from
        the class-level ClassVar this validator inspects.
        """
        super().__init_subclass__(**kwargs)
        prefix = cls.error_code.value // 1000
        expected = CODE_CATEGORY_PREFIX.get(prefix)
        if expected is not None and cls.error_category != expected:
            msg = (
                f"{cls.__name__}: error_code {cls.error_code.name} "
                f"(prefix {prefix}) implies category {expected.name}, "
                f"but error_category is {cls.error_category.name}"
            )
            raise TypeError(msg)


# ── Generic domain exceptions ─────────────────────────────────────


class NotFoundError(DomainError):
    """Raised when a requested resource does not exist (404)."""

    default_message: ClassVar[str] = "Resource not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    status_code: ClassVar[int] = 404


class ConflictError(DomainError):
    """Raised when a resource conflict occurs (409)."""

    default_message: ClassVar[str] = "Resource conflict"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    status_code: ClassVar[int] = 409


class ValidationError(DomainError):
    """Raised when request data fails validation (422)."""

    default_message: ClassVar[str] = "Validation error"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    status_code: ClassVar[int] = 422


class VersionConflictError(ConflictError):
    """Raised when an ETag/If-Match version check fails (409).

    Used for optimistic concurrency at the API boundary (settings ETags,
    workflow definition writes, etc.).  Distinct from
    :class:`synthorg.core.persistence_errors.VersionConflictError`,
    which signals an optimistic-concurrency race at the row level.
    """

    default_message: ClassVar[str] = "Version conflict"
    error_code: ClassVar[ErrorCode] = ErrorCode.VERSION_CONFLICT


class ForbiddenError(DomainError):
    """Raised when access is denied (403)."""

    default_message: ClassVar[str] = "Forbidden"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    error_code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    status_code: ClassVar[int] = 403


class UnauthorizedError(DomainError):
    """Raised when authentication is required or invalid (401)."""

    default_message: ClassVar[str] = "Authentication required"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    error_code: ClassVar[ErrorCode] = ErrorCode.UNAUTHORIZED
    status_code: ClassVar[int] = 401


class SessionRevokedError(UnauthorizedError):
    """Raised when a revoked session token is used (401).

    Distinct ``error_code`` (``SESSION_REVOKED``) lets clients render
    a "you were logged out" message instead of a generic auth failure.
    """

    default_message: ClassVar[str] = "Session has been revoked"
    error_code: ClassVar[ErrorCode] = ErrorCode.SESSION_REVOKED


class RefreshTokenInvalidError(UnauthorizedError):
    """Raised when a presented refresh token cannot rotate (401).

    Covers a missing / expired / replayed refresh token and a
    revoked session. Distinct ``error_code``
    (``REFRESH_TOKEN_INVALID``) lets the client distinguish "your
    refresh failed, log in again" from a generic 401.
    """

    default_message: ClassVar[str] = "Refresh token is invalid or expired"
    error_code: ClassVar[ErrorCode] = ErrorCode.REFRESH_TOKEN_INVALID


class AccountLockedError(DomainError):
    """Raised when login is blocked by account lockout (429).

    Uses HTTP 429 (Too Many Requests) with an optional ``Retry-After``
    header indicating when the lockout expires.

    Attributes:
        retry_after: Seconds the client should wait before retrying;
            clamped to non-negative.  Declared in the class body so
            external readers and IDE tooling see the field without
            needing to inspect ``__init__``.
    """

    default_message: ClassVar[str] = "Account temporarily locked"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    error_code: ClassVar[ErrorCode] = ErrorCode.ACCOUNT_LOCKED
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 429

    retry_after: int

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int = 0,
    ) -> None:
        super().__init__(message)
        self.retry_after = max(0, int(retry_after))


class ServiceUnavailableError(DomainError):
    """Raised when a required service is not configured or is down (503)."""

    default_message: ClassVar[str] = "Service unavailable"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.SERVICE_UNAVAILABLE
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 503


class FeatureNotImplementedError(DomainError):
    """Raised when a feature is not supported by the active backend (501).

    Distinct from ``ServiceUnavailableError`` (503): 503 means a known
    service is currently down or unconfigured, retry later might
    succeed.  501 means the active backend or deployment fundamentally
    does not implement the requested operation -- retrying without
    changing configuration will not help.
    """

    default_message: ClassVar[str] = "Feature not implemented"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.FEATURE_NOT_IMPLEMENTED
    status_code: ClassVar[int] = 501


class ArtifactRejectedTooLargeError(DomainError):
    """Raised when an artifact upload exceeds the configured size limit (413).

    Distinct from :class:`synthorg.core.persistence_errors.ArtifactTooLargeError`,
    which fires when a storage backend rejects an oversized object.
    """

    default_message: ClassVar[str] = "Artifact content is too large"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.ARTIFACT_TOO_LARGE
    status_code: ClassVar[int] = 413


class ProviderTierCoverageInsufficientError(ValidationError):
    """Raised when configured providers expose no models during setup (422).

    The setup-wizard contract requires at least one configured provider
    that exposes at least one model before the company step can apply
    a template. Distinct ``error_code`` lets the dashboard route the
    operator back to the providers step instead of showing a generic
    Retry button (the action would always fail until a model is added).
    """

    default_message: ClassVar[str] = (
        "No configured provider exposes any models. "
        "Go back to the Providers step, add at least one model "
        "to a provider, then return here to apply the template."
    )
    error_code: ClassVar[ErrorCode] = ErrorCode.PROVIDER_TIER_COVERAGE_INSUFFICIENT


class ArtifactStorageRejectedFullError(DomainError):
    """Raised when the artifact-storage subsystem reports it is full (507).

    Distinct from
    :class:`synthorg.core.persistence_errors.ArtifactStorageFullError`,
    which is the persistence-layer signal; this is the API-boundary
    rejection raised when the controller observes the same condition.
    """

    default_message: ClassVar[str] = "Artifact storage is full"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.ARTIFACT_STORAGE_FULL
    status_code: ClassVar[int] = 507


class ArtifactPersistenceNoStorageError(DomainError):
    """Raised when an artifact-content delete is requested without a backend.

    Surfaces a controller-helper bug: ``ArtifactService`` was constructed
    without a ``storage`` dependency, so ``delete_with_content`` cannot
    run.  Distinct ``error_code`` so dashboards can identify the
    misconfiguration.
    """

    default_message: ClassVar[str] = "Artifact service is missing a storage backend"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.ARTIFACT_NO_STORAGE_BACKEND
    status_code: ClassVar[int] = 500


class PerOperationRateLimitError(DomainError):
    """Raised when a per-operation rate limit is exceeded (429).

    Produced by :func:`synthorg.api.rate_limits.guard.per_op_rate_limit`
    guards.  The exception handler emits ``Retry-After`` when
    ``retry_after`` is set.

    Attributes:
        retry_after: Seconds the client should wait before retrying;
            always at least 1 to keep clients from hot-looping on a
            sub-second window.
    """

    default_message: ClassVar[str] = "Rate limit exceeded"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.RATE_LIMIT
    error_code: ClassVar[ErrorCode] = ErrorCode.PER_OPERATION_RATE_LIMITED
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 429

    retry_after: int

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int = 1,
    ) -> None:
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))


class ConcurrencyLimitExceededError(PerOperationRateLimitError):
    """Raised when a per-operation concurrency (inflight) cap is hit (429).

    Produced by ``PerOpConcurrencyMiddleware`` when a user already has
    ``max_inflight`` requests running for the guarded operation.
    Inherits from :class:`PerOperationRateLimitError` so the existing
    429 / ``Retry-After`` / RFC 9457 handling applies unchanged; a
    distinct ``error_code`` lets clients discriminate concurrency
    denials ("you already have one running") from window denials
    ("try again after the bucket refills").
    """

    default_message: ClassVar[str] = "Concurrency limit exceeded"
    error_code: ClassVar[ErrorCode] = ErrorCode.CONCURRENCY_LIMIT_EXCEEDED


# ── Factories ─────────────────────────────────────────────────────


def resource_not_found(
    resource_type: str,
    identifier: str,
    *,
    code: ErrorCode = ErrorCode.RESOURCE_NOT_FOUND,
) -> NotFoundError:
    """Build a :class:`NotFoundError` with a structured message + code.

    Callers should prefer the domain-specific ``ErrorCode`` (e.g.
    ``ErrorCode.TASK_NOT_FOUND``) so API clients can discriminate which
    resource was missing without parsing the message.  The fallback
    ``RESOURCE_NOT_FOUND`` covers resources that don't yet have a
    dedicated code.

    Args:
        resource_type: Human-readable type (``"task"``, ``"agent"``).
        identifier: The missing identifier value.
        code: Specific error code for the resource (defaults to the
            generic ``RESOURCE_NOT_FOUND``).  Must be a 3xxx
            NOT_FOUND-category code; passing a code outside that range
            would emit a 404 response carrying a non-NOT_FOUND machine
            code, breaking the taxonomy contract for clients.

    Returns:
        A :class:`NotFoundError` whose message is
        ``"{resource_type} {identifier!r} not found"`` and whose
        ``error_code`` is ``code``.

    Raises:
        ValueError: If ``code`` is not a 3xxx NOT_FOUND-category code.
    """
    if code.value // 1000 != NOT_FOUND_BAND:
        msg = (
            "resource_not_found requires a NOT_FOUND (3xxx) ErrorCode; "
            f"got {code.name} ({code.value})"
        )
        raise ValueError(msg)
    error = NotFoundError(f"{resource_type} {identifier!r} not found")
    # ``error_code`` is a ClassVar on the base class; the factory assigns
    # an instance attribute so this particular raise reports the
    # resource-specific code while reusing the shared class.
    error.error_code = code  # type: ignore[misc]
    return error
