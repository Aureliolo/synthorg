"""API error hierarchy.

All API-specific errors inherit from ``ApiError`` so callers can catch
the entire family with a single except clause.  The taxonomy enums
(``ErrorCategory`` / ``ErrorCode``) and RFC 9457 helper functions live
in :mod:`synthorg.core.error_taxonomy`; this module imports them so
the API-layer concrete subclasses can stamp their metadata.
"""

from typing import ClassVar

from synthorg.core.error_taxonomy import (
    CATEGORY_TITLES,
    CODE_CATEGORY_PREFIX,
    NOT_FOUND_BAND,
    ErrorCategory,
    ErrorCode,
    category_title,
    category_type_uri,
)

__all__ = [
    "CATEGORY_TITLES",
    "AccountLockedError",
    "ApiError",
    "ApiValidationError",
    "ArtifactStorageFullApiError",
    "ArtifactTooLargeApiError",
    "ConcurrencyLimitExceededError",
    "ConflictError",
    "ErrorCategory",
    "ErrorCode",
    "ForbiddenError",
    "NotFoundError",
    "PerOperationRateLimitError",
    "ServiceUnavailableError",
    "SessionRevokedError",
    "UnauthorizedError",
    "VersionConflictError",
    "category_title",
    "category_type_uri",
    "resource_not_found",
]


class ApiError(Exception):
    """Base exception for API-layer errors.

    Class Attributes:
        default_message: Fallback error message used when none is provided
            and for 5xx response scrubbing.
        error_category: RFC 9457 error category.
        error_code: RFC 9457 machine-readable error code.
        retryable: Whether the client should retry the request.

    Instance Attributes:
        status_code: HTTP status code (set via ``__init__``, fixed per
            subclass).
    """

    default_message: ClassVar[str] = "Internal server error"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    retryable: ClassVar[bool] = False

    def __init__(self, message: str | None = None, *, status_code: int = 500) -> None:
        super().__init__(message or self.default_message)
        self.status_code = status_code

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Validate error_code/error_category consistency at class creation."""
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


class NotFoundError(ApiError):
    """Raised when a requested resource does not exist (404)."""

    default_message: ClassVar[str] = "Resource not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=404)


def resource_not_found(
    resource_type: str,
    identifier: str,
    *,
    code: ErrorCode = ErrorCode.RESOURCE_NOT_FOUND,
) -> NotFoundError:
    """Build a :class:`NotFoundError` with a structured message + code.

    Callers should prefer the domain-specific ``ErrorCode`` (e.g.
    ``ErrorCode.TASK_NOT_FOUND``) so API clients can discriminate
    which resource was missing without parsing the message.  The
    fallback ``RESOURCE_NOT_FOUND`` covers resources that don't yet
    have a dedicated code.

    Args:
        resource_type: Human-readable type (``"task"``, ``"agent"``).
        identifier: The missing identifier value.
        code: Specific error code for the resource (defaults to
            the generic ``RESOURCE_NOT_FOUND``). Must be a 3xxx
            NOT_FOUND-category code; passing a code outside that
            range would emit a 404 response carrying a non-NOT_FOUND
            machine code, breaking the taxonomy contract for clients.

    Returns:
        A ``NotFoundError`` whose message is
        ``"{resource_type} {identifier!r} not found"`` and whose
        ``error_code`` is ``code``.

    Raises:
        ValueError: If ``code`` is not a 3xxx NOT_FOUND-category code.
    """
    # ``ErrorCode`` groups codes in 1000-wide bands by category; the
    # 3xxx band is reserved for NOT_FOUND per the enum docstring.
    if code.value // 1000 != NOT_FOUND_BAND:
        msg = (
            "resource_not_found requires a NOT_FOUND (3xxx) ErrorCode; "
            f"got {code.name} ({code.value})"
        )
        raise ValueError(msg)
    error = NotFoundError(f"{resource_type} {identifier!r} not found")
    # ``error_code`` is a ClassVar on the base class; the factory
    # assigns an instance attribute so this particular raise reports
    # the resource-specific code while reusing the shared class.
    error.error_code = code  # type: ignore[misc]
    return error


class ApiValidationError(ApiError):
    """Raised when request data fails validation (422)."""

    default_message: ClassVar[str] = "Validation error"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=422)


class ConflictError(ApiError):
    """Raised when a resource conflict occurs (409)."""

    default_message: ClassVar[str] = "Resource conflict"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=409)


class VersionConflictError(ApiError):
    """Raised when an ETag/If-Match version check fails (409).

    Used for ETag/If-Match optimistic concurrency checks,
    currently on settings endpoints.
    """

    default_message: ClassVar[str] = "Version conflict"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.VERSION_CONFLICT

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=409)


class ForbiddenError(ApiError):
    """Raised when access is denied (403)."""

    default_message: ClassVar[str] = "Forbidden"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    error_code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=403)


class SessionRevokedError(ApiError):
    """Raised when a revoked session token is used (401).

    Gives clients a distinct error code (``SESSION_REVOKED``) so
    they can show a "you were logged out" message instead of a
    generic auth failure.
    """

    default_message: ClassVar[str] = "Session has been revoked"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    error_code: ClassVar[ErrorCode] = ErrorCode.SESSION_REVOKED

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=401)


class UnauthorizedError(ApiError):
    """Raised when authentication is required or invalid (401)."""

    default_message: ClassVar[str] = "Authentication required"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    error_code: ClassVar[ErrorCode] = ErrorCode.UNAUTHORIZED

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=401)


class AccountLockedError(ApiError):
    """Raised when login is blocked by account lockout (429).

    Uses HTTP 429 (Too Many Requests) with an optional
    ``Retry-After`` header indicating when the lockout expires.
    """

    default_message: ClassVar[str] = "Account temporarily locked"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    error_code: ClassVar[ErrorCode] = ErrorCode.ACCOUNT_LOCKED
    retryable: ClassVar[bool] = True

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int = 0,
    ) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = max(0, int(retry_after))


class ServiceUnavailableError(ApiError):
    """Raised when a required service is not configured (503)."""

    default_message: ClassVar[str] = "Service unavailable"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.SERVICE_UNAVAILABLE
    retryable: ClassVar[bool] = True

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=503)


class ArtifactTooLargeApiError(ApiError):
    """Raised when an artifact upload exceeds the size limit (413)."""

    default_message: ClassVar[str] = "Artifact content is too large"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.ARTIFACT_TOO_LARGE

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=413)


class ArtifactStorageFullApiError(ApiError):
    """Raised when the artifact storage backend is full (507)."""

    default_message: ClassVar[str] = "Artifact storage is full"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.ARTIFACT_STORAGE_FULL

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=507)


class PerOperationRateLimitError(ApiError):
    """Raised when a per-operation rate limit is exceeded (429).

    Produced by :func:`synthorg.api.rate_limits.guard.per_op_rate_limit`
    guards. Flows through ``handle_api_error`` to produce an RFC 9457
    response with ``Retry-After`` set.
    """

    default_message: ClassVar[str] = "Rate limit exceeded"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.RATE_LIMIT
    error_code: ClassVar[ErrorCode] = ErrorCode.PER_OPERATION_RATE_LIMITED
    retryable: ClassVar[bool] = True

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int = 1,
    ) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = max(1, int(retry_after))


class ConcurrencyLimitExceededError(PerOperationRateLimitError):
    """Raised when a per-operation concurrency (inflight) cap is hit (429).

    Produced by the ``PerOpConcurrencyMiddleware`` when a user already
    has ``max_inflight`` requests running for the guarded operation.
    Inherits from :class:`PerOperationRateLimitError` so the existing
    429 / ``Retry-After`` / RFC 9457 handling applies unchanged.  A
    distinct ``error_code`` lets clients discriminate concurrency
    denials ("you already have one running") from window denials
    ("try again after the bucket refills").
    """

    default_message: ClassVar[str] = "Concurrency limit exceeded"
    error_code: ClassVar[ErrorCode] = ErrorCode.CONCURRENCY_LIMIT_EXCEEDED
