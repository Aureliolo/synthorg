"""Exception handlers mapping domain errors to HTTP responses.

Each handler returns an ``ApiResponse(error=...)`` with the
appropriate HTTP status code.  5xx responses return a generic
scrubbed message; 4xx responses pass through the exception detail
(authored by SynthOrg's guards/middleware and user-safe).  Detailed
error context is logged server-side for all status codes.
"""

from http import HTTPStatus
from typing import Any, Final

from litestar import Request, Response
from litestar.exceptions import (
    HTTPException,
    NotAuthorizedException,
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)

from synthorg.api.dto import ApiResponse
from synthorg.api.errors import ApiError
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_REQUEST_ERROR,
    API_ROUTE_NOT_FOUND,
)
from synthorg.persistence.errors import (
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
    detail = exc.detail or HTTPStatus.FORBIDDEN.phrase
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
    detail = exc.detail or HTTPStatus.UNAUTHORIZED.phrase
    return Response(
        content=ApiResponse[None](error=detail),
        status_code=401,
    )


def handle_not_found(
    request: Request[Any, Any, Any],
    exc: NotFoundException,
) -> Response[ApiResponse[None]]:
    """Map Litestar ``NotFoundException`` to 404.

    Ensures unmatched routes return 404 instead of falling through
    to ``handle_unexpected`` (which returns 500), which ZAP flags
    as a security misconfiguration.
    """
    logger.warning(
        API_ROUTE_NOT_FOUND,
        method=request.method,
        path=str(request.url.path),
        status_code=404,
        error_type=type(exc).__qualname__,
        error=str(exc),
    )
    return Response(
        content=ApiResponse[None](error="Not found"),
        status_code=404,
    )


def handle_http_exception(
    request: Request[Any, Any, Any],
    exc: HTTPException,
) -> Response[ApiResponse[None]]:
    """Catch-all for unhandled Litestar ``HTTPException`` subclasses.

    Preserves the correct status code (e.g. 405, 429) instead of
    letting them fall through to ``handle_unexpected`` as 500.
    """
    status = exc.status_code
    _log_error(request, exc, status=status)
    if status >= _SERVER_ERROR_THRESHOLD:
        msg = "Internal server error"
    else:
        try:
            fallback = HTTPStatus(status).phrase
        except ValueError:
            fallback = "Request error"
        msg = exc.detail or fallback
    return Response(
        content=ApiResponse[None](error=msg),
        status_code=status,
        headers=exc.headers,
    )


# Litestar resolves exception handlers by walking the raised exception's
# MRO — the first matching type found in this dict wins.  Dict insertion
# order does NOT affect resolution priority.  (For HTTPException subclasses,
# Litestar also checks integer status-code keys first, but this dict uses
# only type keys.)
EXCEPTION_HANDLERS: dict[type[Exception], object] = {
    RecordNotFoundError: handle_record_not_found,
    DuplicateRecordError: handle_duplicate_record,
    PersistenceError: handle_persistence_error,
    NotAuthorizedException: handle_not_authorized,
    PermissionDeniedException: handle_permission_denied,
    ValidationException: handle_validation_error,
    NotFoundException: handle_not_found,
    HTTPException: handle_http_exception,
    ApiError: handle_api_error,
    Exception: handle_unexpected,
}
