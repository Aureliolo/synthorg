"""API error hierarchy and RFC 9457 error taxonomy.

All API-specific errors inherit from ``ApiError`` so callers
can catch the entire family with a single except clause.

``ErrorCategory`` and ``ErrorCode`` provide machine-readable error
metadata for structured error responses (RFC 9457).
"""

from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import ClassVar, Final


class ErrorCategory(StrEnum):
    """High-level error category for structured error responses.

    Values are lowercase strings suitable for JSON serialization.
    """

    AUTH = "auth"
    VALIDATION = "validation"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMIT = "rate_limit"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROVIDER_ERROR = "provider_error"
    INTERNAL = "internal"


class ErrorCode(IntEnum):
    """Machine-readable error codes (4-digit, category-grouped).

    First digit encodes the category:
    1xxx = auth, 2xxx = validation, 3xxx = not_found, 4xxx = conflict,
    5xxx = rate_limit, 6xxx = budget_exhausted, 7xxx = provider_error,
    8xxx = internal.
    """

    # 1xxx -- auth
    UNAUTHORIZED = 1000
    FORBIDDEN = 1001
    SESSION_REVOKED = 1002
    ACCOUNT_LOCKED = 1003
    CSRF_REJECTED = 1004
    REFRESH_TOKEN_INVALID = 1005
    SESSION_LIMIT_EXCEEDED = 1006
    TOOL_PERMISSION_DENIED = 1007

    # 2xxx -- validation
    VALIDATION_ERROR = 2000
    REQUEST_VALIDATION_ERROR = 2001
    ARTIFACT_TOO_LARGE = 2002
    TOOL_PARAMETER_ERROR = 2003

    # 3xxx -- not_found
    RESOURCE_NOT_FOUND = 3000
    RECORD_NOT_FOUND = 3001
    ROUTE_NOT_FOUND = 3002
    PROJECT_NOT_FOUND = 3003
    TASK_NOT_FOUND = 3004
    SUBWORKFLOW_NOT_FOUND = 3005
    WORKFLOW_EXECUTION_NOT_FOUND = 3006
    CHANNEL_NOT_FOUND = 3007
    TOOL_NOT_FOUND = 3008
    ONTOLOGY_NOT_FOUND = 3009
    CONNECTION_NOT_FOUND = 3010
    MODEL_NOT_FOUND = 3011
    ESCALATION_NOT_FOUND = 3012

    # 4xxx -- conflict
    RESOURCE_CONFLICT = 4000
    DUPLICATE_RECORD = 4001
    VERSION_CONFLICT = 4002
    TASK_VERSION_CONFLICT = 4003
    ONTOLOGY_DUPLICATE = 4004
    CHANNEL_ALREADY_EXISTS = 4005
    ESCALATION_ALREADY_DECIDED = 4006
    MIXED_CURRENCY_AGGREGATION = 4007

    # 5xxx -- rate_limit
    RATE_LIMITED = 5000
    PER_OPERATION_RATE_LIMITED = 5001
    CONCURRENCY_LIMIT_EXCEEDED = 5002

    # 6xxx -- budget_exhausted
    BUDGET_EXHAUSTED = 6000
    DAILY_LIMIT_EXCEEDED = 6001
    RISK_BUDGET_EXHAUSTED = 6002
    PROJECT_BUDGET_EXHAUSTED = 6003
    QUOTA_EXHAUSTED = 6004

    # 7xxx -- provider_error
    PROVIDER_ERROR = 7000
    PROVIDER_TIMEOUT = 7001
    PROVIDER_CONNECTION = 7002
    PROVIDER_INTERNAL = 7003
    PROVIDER_AUTHENTICATION_FAILED = 7004
    PROVIDER_INVALID_REQUEST = 7005
    PROVIDER_CONTENT_FILTERED = 7006
    INTEGRATION_ERROR = 7007
    OAUTH_ERROR = 7008
    WEBHOOK_ERROR = 7009

    # 8xxx -- internal
    INTERNAL_ERROR = 8000
    SERVICE_UNAVAILABLE = 8001
    PERSISTENCE_ERROR = 8002
    ENGINE_ERROR = 8003
    ONTOLOGY_ERROR = 8004
    COMMUNICATION_ERROR = 8005
    TOOL_ERROR = 8006
    ARTIFACT_STORAGE_FULL = 8007
    TOOL_EXECUTION_ERROR = 8008


# Error-code band for the NOT_FOUND category (3xxx). Used by
# ``resource_not_found`` to reject non-NOT_FOUND codes so a 404
# cannot accidentally carry an auth / validation / conflict code.
_NOT_FOUND_BAND: Final[int] = 3

# Maps first digit of error code to its expected category.
# Used by ``__init_subclass__`` to validate that error code prefixes
# match their declared category.
_CODE_CATEGORY_PREFIX: MappingProxyType[int, ErrorCategory] = MappingProxyType(
    {
        1: ErrorCategory.AUTH,
        2: ErrorCategory.VALIDATION,
        3: ErrorCategory.NOT_FOUND,
        4: ErrorCategory.CONFLICT,
        5: ErrorCategory.RATE_LIMIT,
        6: ErrorCategory.BUDGET_EXHAUSTED,
        7: ErrorCategory.PROVIDER_ERROR,
        8: ErrorCategory.INTERNAL,
    }
)


CATEGORY_TITLES: MappingProxyType[ErrorCategory, str] = MappingProxyType(
    {
        ErrorCategory.AUTH: "Authentication Error",
        ErrorCategory.VALIDATION: "Validation Error",
        ErrorCategory.NOT_FOUND: "Resource Not Found",
        ErrorCategory.CONFLICT: "Resource Conflict",
        ErrorCategory.RATE_LIMIT: "Rate Limit Exceeded",
        ErrorCategory.BUDGET_EXHAUSTED: "Budget Exhausted",
        ErrorCategory.PROVIDER_ERROR: "Provider Error",
        ErrorCategory.INTERNAL: "Internal Server Error",
    }
)

_ERROR_DOCS_BASE: Final[str] = "https://synthorg.io/docs/errors"


def category_title(cat: ErrorCategory) -> str:
    """Return the RFC 9457 ``title`` for a category.

    Args:
        cat: Error category.

    Returns:
        Human-readable title string.
    """
    return CATEGORY_TITLES[cat]


def category_type_uri(cat: ErrorCategory) -> str:
    """Return the RFC 9457 ``type`` URI for a category.

    Args:
        cat: Error category.

    Returns:
        Documentation URI with fragment anchor for the error category.
    """
    return f"{_ERROR_DOCS_BASE}#{cat.value}"


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
        expected = _CODE_CATEGORY_PREFIX.get(prefix)
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
    if code.value // 1000 != _NOT_FOUND_BAND:
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
