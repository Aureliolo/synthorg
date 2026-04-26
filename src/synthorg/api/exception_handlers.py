"""Exception handlers mapping domain errors to HTTP responses.

Each handler returns either an ``ApiResponse`` envelope (default) or a
bare RFC 9457 ``ProblemDetail`` body when the client sends
``Accept: application/problem+json``.

5xx responses return a generic scrubbed message; 4xx responses pass
through the exception detail (authored by SynthOrg's guards/middleware
and user-safe).  Detailed error context is logged server-side for all
status codes.

All handlers populate structured RFC 9457 metadata (error code, category,
retryability, title, type URI, request correlation ID).
"""

import contextlib
import math
from http import HTTPStatus
from types import MappingProxyType
from typing import Any, Final

import structlog
from litestar import Request, Response
from litestar.exceptions import (
    HTTPException,
    NotAuthorizedException,
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)

from synthorg.api.cursor import InvalidCursorError
from synthorg.api.dto import ApiResponse, ErrorDetail, ProblemDetail
from synthorg.api.errors import (
    ApiError,
    ErrorCategory,
    ErrorCode,
    category_title,
    category_type_uri,
)
from synthorg.budget.errors import (
    BudgetExhaustedError,
    MixedCurrencyAggregationError,
)
from synthorg.communication.errors import CommunicationError
from synthorg.engine.errors import EngineError
from synthorg.integrations.errors import IntegrationError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.correlation import generate_correlation_id
from synthorg.observability.events.api import (
    API_ACCEPT_PARSE_FAILED,
    API_CONTENT_NEGOTIATED,
    API_CORRELATION_FALLBACK,
    API_REQUEST_ERROR,
    API_ROUTE_NOT_FOUND,
)
from synthorg.observability.metrics_hub import record_api_error
from synthorg.ontology.errors import OntologyError
from synthorg.persistence.errors import (
    DuplicateRecordError,
    PersistenceError,
    RecordNotFoundError,
)
from synthorg.providers.errors import ProviderError, RateLimitError
from synthorg.tools.errors import ToolError

logger = get_logger(__name__)

_SERVER_ERROR_THRESHOLD: Final[int] = 500

_PROBLEM_JSON: Final[str] = "application/problem+json"

_MAX_DETAIL_LEN: Final[int] = 512

# Headers safe to forward from HTTPException to the client response.
_ALLOWED_PASSTHROUGH_HEADERS: Final[frozenset[str]] = frozenset(
    {"retry-after", "www-authenticate", "allow"},
)


def _get_instance_id() -> str:
    """Get request correlation ID from structlog context, or generate one.

    Wrapped defensively because this runs inside exception handlers,
    which are the last line of defense and must never crash.
    """
    try:
        ctx = structlog.contextvars.get_contextvars()
        request_id = ctx.get("request_id")
        if isinstance(request_id, str) and request_id:
            return request_id
    except Exception as exc:
        logger.warning(
            API_CORRELATION_FALLBACK,
            error_type=type(exc).__qualname__,
            error=safe_error_description(exc),
        )
    return generate_correlation_id()


def _wants_problem_json(request: Request[Any, Any, Any]) -> bool:
    """Check whether the client prefers ``application/problem+json``.

    Returns ``True`` only when the Accept header explicitly prefers
    ``application/problem+json`` over ``application/json``.  Defaults
    to ``False`` for ``*/*``, missing, or empty Accept headers.

    Wrapped defensively because this runs inside exception handlers,
    which are the last line of defense and must never crash.
    """
    try:
        match = request.accept.best_match(
            ["application/json", _PROBLEM_JSON],
        )
    except Exception as exc:
        logger.warning(
            API_ACCEPT_PARSE_FAILED,
            error_type=type(exc).__qualname__,
            error=safe_error_description(exc),
        )
        return False
    return match == _PROBLEM_JSON


def _build_error_response(
    *,
    detail: str,
    error_code: ErrorCode,
    error_category: ErrorCategory,
    retryable: bool = False,
    retry_after: int | None = None,
) -> ApiResponse[None]:
    """Build an ``ApiResponse`` with structured ``ErrorDetail``.

    The ``instance`` field is auto-populated from the current structlog
    request context (falling back to a newly generated correlation ID
    if unavailable).
    """
    return ApiResponse[None](
        error=detail,
        error_detail=ErrorDetail(
            detail=detail,
            error_code=error_code,
            error_category=error_category,
            retryable=retryable,
            retry_after=retry_after,
            instance=_get_instance_id(),
            title=category_title(error_category),
            type=category_type_uri(error_category),
        ),
    )


def _build_problem_detail_response(  # noqa: PLR0913
    *,
    detail: str,
    error_code: ErrorCode,
    error_category: ErrorCategory,
    status_code: int,
    retryable: bool,
    retry_after: int | None,
    headers: dict[str, str] | None,
) -> Response[ProblemDetail]:
    """Build a bare RFC 9457 ``application/problem+json`` response."""
    return Response(
        content=ProblemDetail(
            type=category_type_uri(error_category),
            title=category_title(error_category),
            status=status_code,
            detail=detail,
            instance=_get_instance_id(),
            error_code=error_code,
            error_category=error_category,
            retryable=retryable,
            retry_after=retry_after,
        ),
        status_code=status_code,
        media_type=_PROBLEM_JSON,
        headers=headers,
    )


def _build_response(  # noqa: PLR0913
    request: Request[Any, Any, Any],
    *,
    detail: str,
    error_code: ErrorCode,
    error_category: ErrorCategory,
    status_code: int,
    retryable: bool = False,
    retry_after: int | None = None,
    headers: dict[str, str] | None = None,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Build either an envelope or bare RFC 9457 response.

    Content negotiation is driven by the client's ``Accept`` header.
    When ``application/problem+json`` is preferred, returns a bare
    ``ProblemDetail`` body with the appropriate content type.

    Wrapped in a defensive try/except because this runs inside
    exception handlers -- a failure here would lose the original error.
    """
    # Every 4xx/5xx response emits a classification counter so
    # operators can observe error-category rates without digging
    # through logs. Record here (before response build) so a
    # response-build failure still emits the metric.
    if status_code >= 400:  # noqa: PLR2004
        record_api_error(category=error_category.value, status_code=status_code)
    try:
        if _wants_problem_json(request):
            logger.debug(
                API_CONTENT_NEGOTIATED,
                format="problem+json",
                status_code=status_code,
            )
            return _build_problem_detail_response(
                detail=detail,
                error_code=error_code,
                error_category=error_category,
                status_code=status_code,
                retryable=retryable,
                retry_after=retry_after,
                headers=headers,
            )
        return Response(
            content=_build_error_response(
                detail=detail,
                error_code=error_code,
                error_category=error_category,
                retryable=retryable,
                retry_after=retry_after,
            ),
            status_code=status_code,
            headers=headers,
        )
    except Exception:
        logger.error(
            API_REQUEST_ERROR,
            error_type="response_build_failure",
            error="Failed to build structured error response",
            detail=detail,
            original_status_code=status_code,
            exc_info=True,
        )
        return Response(  # type: ignore[return-value]
            content={"error": "Internal server error"},
            status_code=500,
            media_type="application/json",
        )


_STATUS_TO_ERROR_META: MappingProxyType[int, tuple[ErrorCode, ErrorCategory, bool]] = (
    MappingProxyType(
        {
            401: (ErrorCode.UNAUTHORIZED, ErrorCategory.AUTH, False),
            403: (ErrorCode.FORBIDDEN, ErrorCategory.AUTH, False),
            404: (ErrorCode.ROUTE_NOT_FOUND, ErrorCategory.NOT_FOUND, False),
            409: (ErrorCode.RESOURCE_CONFLICT, ErrorCategory.CONFLICT, False),
            429: (ErrorCode.RATE_LIMITED, ErrorCategory.RATE_LIMIT, True),
            503: (ErrorCode.SERVICE_UNAVAILABLE, ErrorCategory.INTERNAL, True),
        }
    )
)

_CLIENT_ERROR_DEFAULT: tuple[ErrorCode, ErrorCategory, bool] = (
    ErrorCode.REQUEST_VALIDATION_ERROR,
    ErrorCategory.VALIDATION,
    False,
)

_SERVER_ERROR_DEFAULT: tuple[ErrorCode, ErrorCategory, bool] = (
    ErrorCode.INTERNAL_ERROR,
    ErrorCategory.INTERNAL,
    False,
)


def _category_for_status(
    status: int,
) -> tuple[ErrorCode, ErrorCategory, bool]:
    """Map HTTP status to error code, category, and retryability."""
    if status in _STATUS_TO_ERROR_META:
        return _STATUS_TO_ERROR_META[status]
    if status >= _SERVER_ERROR_THRESHOLD:
        return _SERVER_ERROR_DEFAULT
    return _CLIENT_ERROR_DEFAULT


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
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``RecordNotFoundError`` to 404."""
    _log_error(request, exc, status=404)
    return _build_response(
        request,
        detail="Resource not found",
        error_code=ErrorCode.RECORD_NOT_FOUND,
        error_category=ErrorCategory.NOT_FOUND,
        status_code=404,
    )


def handle_duplicate_record(
    request: Request[Any, Any, Any],
    exc: DuplicateRecordError,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``DuplicateRecordError`` to 409."""
    _log_error(request, exc, status=409)
    return _build_response(
        request,
        detail="Resource already exists",
        error_code=ErrorCode.DUPLICATE_RECORD,
        error_category=ErrorCategory.CONFLICT,
        status_code=409,
    )


def handle_persistence_error(
    request: Request[Any, Any, Any],
    exc: PersistenceError,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``PersistenceError`` to 500."""
    _log_error(request, exc, status=500)
    return _build_response(
        request,
        detail="Internal server error",
        error_code=ErrorCode.PERSISTENCE_ERROR,
        error_category=ErrorCategory.INTERNAL,
        status_code=500,
    )


def handle_api_error(
    request: Request[Any, Any, Any],
    exc: ApiError,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``ApiError`` subclasses to their declared status code."""
    _log_error(request, exc, status=exc.status_code)
    # For 5xx errors return the generic class-level default to avoid
    # leaking internals.  For 4xx client errors return the actual
    # exception message -- it was set by the controller and is user-safe.
    if exc.status_code >= _SERVER_ERROR_THRESHOLD:
        msg = type(exc).default_message
    else:
        msg = str(exc)
    retry_after_raw = getattr(exc, "retry_after", None)
    retry_after_val: int | None = None
    if (
        retry_after_raw is not None
        and not isinstance(retry_after_raw, bool)
        and isinstance(retry_after_raw, int)
        and retry_after_raw >= 0
    ):
        retry_after_val = retry_after_raw
    headers: dict[str, str] | None = None
    if retry_after_val is not None:
        headers = {"Retry-After": str(retry_after_val)}
    return _build_response(
        request,
        detail=msg,
        error_code=exc.error_code,
        error_category=exc.error_category,
        retryable=exc.retryable,
        retry_after=retry_after_val,
        status_code=exc.status_code,
        headers=headers,
    )


def _normalize_status_code(raw: object) -> int:
    """Coerce a raw ``status_code`` attribute to a valid HTTP error code.

    A non-int attribute or a value outside the 400-599 error range is
    mis-annotation territory; normalize to 500 so the handler cannot
    produce a "successful" error envelope.
    """
    value: int
    try:
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            return 500
        value = int(raw)
    except TypeError, ValueError:
        return 500
    if not (400 <= value <= 599):  # noqa: PLR2004
        return 500
    return value


def _normalize_error_metadata(
    exc: Exception,
) -> tuple[ErrorCode, ErrorCategory]:
    """Return validated ``(error_code, error_category)`` for ``exc``.

    Values that are not members of their respective enums are replaced
    by the generic INTERNAL fallbacks so ``_build_response`` never
    serialises junk.
    """
    raw_code = getattr(exc, "error_code", ErrorCode.INTERNAL_ERROR)
    code = raw_code if isinstance(raw_code, ErrorCode) else ErrorCode.INTERNAL_ERROR
    raw_cat = getattr(exc, "error_category", ErrorCategory.INTERNAL)
    cat = raw_cat if isinstance(raw_cat, ErrorCategory) else ErrorCategory.INTERNAL
    return code, cat


def _determine_retryable(exc: Exception) -> bool:
    """Honour an explicit ``is_retryable`` override, else fall back.

    Subclasses that set ``is_retryable`` (True or False) must have
    that value respected -- a stale ClassVar ``retryable`` elsewhere
    in the MRO would otherwise mask the more specific signal.
    ``retryable`` is consulted only when ``is_retryable`` is not set.
    """
    if hasattr(exc, "is_retryable"):
        return bool(exc.is_retryable)
    return bool(getattr(exc, "retryable", False))


def _select_message(exc: Exception, status_code: int) -> str:
    """Pick a user-safe message for the RFC 9457 envelope.

    5xx responses return the class-level ``default_message`` to avoid
    leaking internal detail; 4xx responses pass through the exception
    message (controller-authored, user-safe).
    """
    if status_code >= _SERVER_ERROR_THRESHOLD:
        return str(getattr(exc, "default_message", "Internal server error"))
    return str(exc) or str(getattr(exc, "default_message", "Request error"))


def _parse_retry_after(raw: object) -> int | None:
    """Validate + round-up ``retry_after`` attribute.

    Accept only finite non-negative numerics (``inf``/``nan`` would
    crash header serialization).  Round up rather than truncate so a
    fractional 0.5s delay is surfaced as at least 1s and clients
    never hot-loop.  Returns ``None`` for anything else.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        return None
    return math.ceil(value)


def handle_domain_error(
    request: Request[Any, Any, Any],
    exc: Exception,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map domain-layer exceptions to RFC 9457 responses.

    Reads HTTP metadata ClassVars declared on the domain error bases
    (``status_code``, ``error_code``, ``error_category``, ``retryable``,
    ``default_message``).  Falls back to 500/INTERNAL for any subclass
    that lacks annotations so the handler is forward-compatible with
    newly added domain errors.

    Handles ``EngineError``, ``BudgetExhaustedError``,
    ``MixedCurrencyAggregationError``, ``ProviderError``,
    ``OntologyError``, ``CommunicationError``, ``IntegrationError``, and
    ``ToolError`` hierarchies -- one handler function covers all eight
    via MRO dispatch.

    5xx responses return the class-level ``default_message`` to avoid
    leaking internal detail; 4xx responses pass through the exception
    message which is controller-authored and user-safe.
    """
    status_code = _normalize_status_code(getattr(exc, "status_code", 500))
    error_code_val, error_category_val = _normalize_error_metadata(exc)
    retryable = _determine_retryable(exc)
    _log_error(request, exc, status=status_code)
    msg = _select_message(exc, status_code)
    retry_after_val = _parse_retry_after(getattr(exc, "retry_after", None))
    # Retry-After header and body field must agree: only emit the header
    # when the error is actually retryable, so 429/503-style envelopes
    # can never claim ``retryable: false`` while handing clients a
    # Retry-After to wait on.
    headers: dict[str, str] | None = None
    if retry_after_val is not None and retryable:
        headers = {"Retry-After": str(retry_after_val)}
    return _build_response(
        request,
        detail=msg,
        error_code=error_code_val,
        error_category=error_category_val,
        retryable=retryable,
        retry_after=retry_after_val if retryable else None,
        status_code=status_code,
        headers=headers,
    )


def handle_unexpected(
    request: Request[Any, Any, Any],
    exc: Exception,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Catch-all for unexpected errors -> 500."""
    _log_error(request, exc, status=500)
    return _build_response(
        request,
        detail="Internal server error",
        error_code=ErrorCode.INTERNAL_ERROR,
        error_category=ErrorCategory.INTERNAL,
        status_code=500,
    )


def handle_permission_denied(
    request: Request[Any, Any, Any],
    exc: PermissionDeniedException,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``PermissionDeniedException`` to 403."""
    _log_error(request, exc, status=403)
    return _build_response(
        request,
        detail="Forbidden",
        error_code=ErrorCode.FORBIDDEN,
        error_category=ErrorCategory.AUTH,
        status_code=403,
    )


def handle_validation_error(
    request: Request[Any, Any, Any],
    exc: ValidationException,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``ValidationException`` to 400."""
    _log_error(request, exc, status=400)
    msg = str(exc.detail) if exc.detail else "Validation error"
    return _build_response(
        request,
        detail=msg,
        error_code=ErrorCode.REQUEST_VALIDATION_ERROR,
        error_category=ErrorCategory.VALIDATION,
        status_code=400,
    )


def handle_invalid_cursor(
    request: Request[Any, Any, Any],
    exc: InvalidCursorError,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map :class:`InvalidCursorError` to 400.

    Cursor tokens are opaque to the client; if tampering or decoding
    fails, surface the original (non-sensitive) message so operators
    can distinguish malformed-base64 from signature-mismatch in logs.
    """
    _log_error(request, exc, status=400)
    detail = str(exc) or "Invalid pagination cursor"
    return _build_response(
        request,
        detail=detail,
        error_code=ErrorCode.REQUEST_VALIDATION_ERROR,
        error_category=ErrorCategory.VALIDATION,
        status_code=400,
    )


def handle_not_authorized(
    request: Request[Any, Any, Any],
    exc: NotAuthorizedException,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``NotAuthorizedException`` to 401."""
    _log_error(request, exc, status=401)
    return _build_response(
        request,
        detail="Authentication required",
        error_code=ErrorCode.UNAUTHORIZED,
        error_category=ErrorCategory.AUTH,
        status_code=401,
    )


def handle_not_found(
    request: Request[Any, Any, Any],
    exc: NotFoundException,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
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
    return _build_response(
        request,
        detail="Not found",
        error_code=ErrorCode.ROUTE_NOT_FOUND,
        error_category=ErrorCategory.NOT_FOUND,
        status_code=404,
    )


def handle_http_exception(
    request: Request[Any, Any, Any],
    exc: HTTPException,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
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
        msg = (exc.detail or fallback)[:_MAX_DETAIL_LEN]
    code, category, retryable = _category_for_status(status)
    # Parse Retry-After header into the body field for agent consumers.
    retry_after: int | None = None
    raw_headers = exc.headers or {}
    raw_retry = raw_headers.get("Retry-After") or raw_headers.get("retry-after")
    if raw_retry:
        with contextlib.suppress(ValueError):
            retry_after = int(raw_retry)
    return _build_response(
        request,
        detail=msg,
        error_code=code,
        error_category=category,
        retryable=retryable,
        retry_after=retry_after,
        status_code=status,
        headers={
            k: v
            for k, v in raw_headers.items()
            if k.lower() in _ALLOWED_PASSTHROUGH_HEADERS
        }
        or None,
    )


# Litestar resolves exception handlers by walking the raised exception's
# MRO -- the first matching type found in this dict wins.  Dict insertion
# order does NOT affect resolution priority.  (For HTTPException subclasses,
# Litestar also checks integer status-code keys first, but this dict uses
# only type keys.)
EXCEPTION_HANDLERS: MappingProxyType[type[Exception], object] = MappingProxyType(
    {
        RecordNotFoundError: handle_record_not_found,
        DuplicateRecordError: handle_duplicate_record,
        PersistenceError: handle_persistence_error,
        NotAuthorizedException: handle_not_authorized,
        PermissionDeniedException: handle_permission_denied,
        ValidationException: handle_validation_error,
        InvalidCursorError: handle_invalid_cursor,
        NotFoundException: handle_not_found,
        HTTPException: handle_http_exception,
        ApiError: handle_api_error,
        # Domain error hierarchies -- MRO dispatch covers every subclass.
        # RateLimitError is listed explicitly so its narrower 429 status
        # takes precedence over the ProviderError (502) default when
        # Litestar walks the raised exception's MRO.
        RateLimitError: handle_domain_error,
        EngineError: handle_domain_error,
        BudgetExhaustedError: handle_domain_error,
        MixedCurrencyAggregationError: handle_domain_error,
        ProviderError: handle_domain_error,
        OntologyError: handle_domain_error,
        CommunicationError: handle_domain_error,
        IntegrationError: handle_domain_error,
        ToolError: handle_domain_error,
        Exception: handle_unexpected,
    }
)
