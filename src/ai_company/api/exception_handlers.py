"""Exception handlers mapping domain errors to HTTP responses.

Each handler returns an ``ApiResponse(success=False)`` with the
appropriate HTTP status code and a user-facing error message.
"""

from typing import Any

from litestar import Request, Response
from litestar.exceptions import PermissionDeniedException

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


def _log_error(
    request: Request[Any, Any, Any],
    exc: Exception,
    *,
    status: int,
) -> None:
    """Log an API error with request context."""
    logger.warning(
        API_REQUEST_ERROR,
        method=request.method,
        path=str(request.url.path),
        status_code=status,
        error_type=type(exc).__qualname__,
        error=str(exc),
    )


def handle_record_not_found(
    request: Request[Any, Any, Any],
    exc: RecordNotFoundError,
) -> Response[ApiResponse[None]]:
    """Map ``RecordNotFoundError`` to 404."""
    _log_error(request, exc, status=404)
    return Response(
        content=ApiResponse[None](success=False, error=str(exc)),
        status_code=404,
    )


def handle_duplicate_record(
    request: Request[Any, Any, Any],
    exc: DuplicateRecordError,
) -> Response[ApiResponse[None]]:
    """Map ``DuplicateRecordError`` to 409."""
    _log_error(request, exc, status=409)
    return Response(
        content=ApiResponse[None](success=False, error=str(exc)),
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
            success=False,
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
    return Response(
        content=ApiResponse[None](success=False, error=str(exc)),
        status_code=exc.status_code,
    )


def handle_value_error(
    request: Request[Any, Any, Any],
    exc: ValueError,
) -> Response[ApiResponse[None]]:
    """Map ``ValueError`` (including Pydantic validation) to 422."""
    _log_error(request, exc, status=422)
    return Response(
        content=ApiResponse[None](success=False, error=str(exc)),
        status_code=422,
    )


def handle_unexpected(
    request: Request[Any, Any, Any],
    exc: Exception,
) -> Response[ApiResponse[None]]:
    """Catch-all for unexpected errors → 500."""
    _log_error(request, exc, status=500)
    return Response(
        content=ApiResponse[None](
            success=False,
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
    return Response(
        content=ApiResponse[None](success=False, error=str(exc)),
        status_code=403,
    )


EXCEPTION_HANDLERS: dict[type[Exception], object] = {
    RecordNotFoundError: handle_record_not_found,
    DuplicateRecordError: handle_duplicate_record,
    PersistenceError: handle_persistence_error,
    PermissionDeniedException: handle_permission_denied,
    ApiError: handle_api_error,
    ValueError: handle_value_error,
    Exception: handle_unexpected,
}
