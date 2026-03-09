"""API error hierarchy.

All API-specific errors inherit from ``ApiError`` so callers
can catch the entire family with a single except clause.
"""


class ApiError(Exception):
    """Base exception for API-layer errors.

    Attributes:
        status_code: HTTP status code associated with this error.
    """

    def __init__(
        self, message: str = "Internal server error", *, status_code: int = 500
    ) -> None:
        super().__init__(message)
        self.status_code = status_code


class NotFoundError(ApiError):
    """Raised when a requested resource does not exist (404)."""

    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, status_code=404)


class ValidationError(ApiError):
    """Raised when request data fails validation (422)."""

    def __init__(self, message: str = "Validation error") -> None:
        super().__init__(message, status_code=422)


class ConflictError(ApiError):
    """Raised when a resource conflict occurs (409)."""

    def __init__(self, message: str = "Resource conflict") -> None:
        super().__init__(message, status_code=409)


class ForbiddenError(ApiError):
    """Raised when access is denied (403)."""

    def __init__(self, message: str = "Forbidden") -> None:
        super().__init__(message, status_code=403)
