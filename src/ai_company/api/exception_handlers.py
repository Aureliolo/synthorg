"""Exception handlers mapping domain errors to HTTP responses.

Each handler returns an ``ApiResponse(error=...)`` with the
appropriate HTTP status code and a **scrubbed** user-facing error
message.  Detailed error context is logged server-side only.
"""

from typing import Any, Final

from litestar import Request, Response
from litestar.exceptions import (
    NotAuthorizedException,
    PermissionDeniedException,
    ValidationException,
)

from ai_company.api.dto import ApiResponse
from ai_company.api.errors import ApiError
from ai_company.observability import get_logger
from ai_company.observability.events.api import API_REQUEST_ERROR
from ai_company.persistence.errors import (
    DuplicateRecordError,
    PersistenceError,
    RecordNotFoundError,
)

logger = get_logger(__name__)

_SERVER_ERROR_THRESHOLD: Final[int] = 500


def _log_error(
    request: Request[Any, Any, Any],
    exc: Exception,
    *,
    status: int,
) -> None:
    """Log an API error with request context.

    Uses ERROR level (with traceback) for 5xx server errors and
    WARNING for 4xx client errors.
    """
    log = logger.error if status >= _SERVER_ERROR_THRESHOLD else logger.warning
    log(
        API_REQUEST_ERROR,
        method=request.method,
        path=str(request.url.path),
        status_code=status,
        error_type=type(exc).__qualname__,
        error=str(exc),
        exc_info=status >= _SERVER_ERROR_THRESHOLD,
    )


def handle_record_not_found(
    request: Request[Any, Any, Any],
    exc: RecordNotFoundError,
) -> Response[ApiResponse[None]]:
    """Map ``RecordNotFoundError`` to 404."""
    _log_error(request, exc, status=404)
    return Response(
        content=ApiResponse[None](
            error="Resource not found",
        ),
        status_code=404,
    )


def handle_duplicate_record(
    request: Request[Any, Any, Any],
    exc: DuplicateRecordError,
) -> Response[ApiResponse[None]]:
    """Map ``DuplicateRecordError`` to 409."""
    _log_error(request, exc, status=409)
    return Response(
        content=ApiResponse[None](
            error="Resource already exists",
        ),
        status_code=409,
    )


def handle_persistence_error(
    request: Request[Any, Any, Any],
    exc: PersistenceError,
) -> Response[ApiResponse[None]]:
    """Map ``PersistenceError`` to 500."""
    _log_error(request, exc, status=500)
    return Response(
        content=ApiResponse[None](
            error="Internal persistence error",
        ),
        status_code=500,
    )


def handle_api_error(
    request: Request[Any, Any, Any],
    exc: ApiError,
) -> Response[ApiResponse[None]]:
    """Map ``ApiError`` subclasses to their declared status code."""
    _log_error(request, exc, status=exc.status_code)
    # For 5xx errors return the generic class-level default to avoid
    # leaking internals.  For 4xx client errors return the actual
    # exception message — it was set by the controller and is user-safe.
    if exc.status_code >= _SERVER_ERROR_THRESHOLD:
        msg = type(exc).default_message
    else:
        msg = str(exc) or type(exc).default_message
    return Response(
        content=ApiResponse[None](error=msg),
        status_code=exc.status_code,
    )


def handle_unexpected(
    request: Request[Any, Any, Any],
    exc: Exception,
) -> Response[ApiResponse[None]]:
    """Catch-all for unexpected errors → 500."""
    _log_error(request, exc, status=500)
    return Response(
        content=ApiResponse[None](
            error="Internal server error",
        ),
        status_code=500,
    )


def handle_permission_denied(
    request: Request[Any, Any, Any],
    exc: PermissionDeniedException,
) -> Response[ApiResponse[None]]:
    """Map ``PermissionDeniedException`` to 403."""
    _log_error(request, exc, status=403)
    detail = exc.detail or "Forbidden"
    return Response(
        content=ApiResponse[None](error=detail),
        status_code=403,
    )


def handle_validation_error(
    request: Request[Any, Any, Any],
    exc: ValidationException,
) -> Response[ApiResponse[None]]:
    """Map ``ValidationException`` to 400."""
    _log_error(request, exc, status=400)
    return Response(
        content=ApiResponse[None](
            error="Validation error",
        ),
        status_code=400,
    )


def handle_not_authorized(
    request: Request[Any, Any, Any],
    exc: NotAuthorizedException,
) -> Response[ApiResponse[None]]:
    """Map ``NotAuthorizedException`` to 401."""
    _log_error(request, exc, status=401)
    detail = exc.detail or "Authentication required"
    return Response(
        content=ApiResponse[None](error=detail),
        status_code=401,
    )


EXCEPTION_HANDLERS: dict[type[Exception], object] = {
    RecordNotFoundError: handle_record_not_found,
    DuplicateRecordError: handle_duplicate_record,
    PersistenceError: handle_persistence_error,
    NotAuthorizedException: handle_not_authorized,
    PermissionDeniedException: handle_permission_denied,
    ValidationException: handle_validation_error,
    ApiError: handle_api_error,
    Exception: handle_unexpected,
}
