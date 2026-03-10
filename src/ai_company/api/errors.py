"""API error hierarchy.

All API-specific errors inherit from ``ApiError`` so callers
can catch the entire family with a single except clause.
"""


class ApiError(Exception):
    """Base exception for API-layer errors.

    Attributes:
        default_message: Class-level default error message.
        status_code: HTTP status code associated with this error.
    """

    default_message: str = "Internal server error"

    def __init__(self, message: str | None = None, *, status_code: int = 500) -> None:
        super().__init__(message or self.default_message)
        self.status_code = status_code


class NotFoundError(ApiError):
    """Raised when a requested resource does not exist (404)."""

    default_message: str = "Resource not found"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=404)


class ApiValidationError(ApiError):
    """Raised when request data fails validation (422)."""

    default_message: str = "Validation error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=422)


class ConflictError(ApiError):
    """Raised when a resource conflict occurs (409)."""

    default_message: str = "Resource conflict"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=409)


class ForbiddenError(ApiError):
    """Raised when access is denied (403)."""

    default_message: str = "Forbidden"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=403)


class UnauthorizedError(ApiError):
    """Raised when authentication is required or invalid (401)."""

    default_message: str = "Authentication required"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=401)


class ServiceUnavailableError(ApiError):
    """Raised when a required service is not configured (503)."""

    default_message: str = "Service unavailable"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, status_code=503)
