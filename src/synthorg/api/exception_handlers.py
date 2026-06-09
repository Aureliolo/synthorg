# module-kind: complex_service
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

One cohesive responsibility: route exceptions to HTTP responses.
The handler table, the content-negotiation chokepoint
(``_build_response``), the safe-log-attrs serialiser, the
status-code / error-code / category / retryability normalisers, and
the per-domain handlers all share the same envelope shape + the same
``_log_error`` call site; splitting handlers into sibling modules
would force the envelope-construction code to live in two places and
break the single redaction chokepoint that keeps tracebacks and
frame-locals out of the sink.
"""

import math
from http import HTTPStatus
from types import MappingProxyType
from typing import Final

import structlog
from litestar import Request, Response
from litestar.datastructures import State
from litestar.exceptions import (
    HTTPException,
    NotAuthorizedException,
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)

from synthorg.a2a.client import A2AClientError
from synthorg.api.auth_response_discriminator import discriminate_unauthorized
from synthorg.api.cursor import InvalidCursorError
from synthorg.api.dto import ApiResponse, ErrorDetail, ProblemDetail
from synthorg.budget.errors import (
    BudgetExhaustedError,
    MixedCurrencyAggregationError,
)
from synthorg.communication.errors import CommunicationError
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import (
    ErrorCategory,
    ErrorCode,
    category_title,
    category_type_uri,
)
from synthorg.core.persistence_errors import (
    ConstraintViolationError,
    DuplicateRecordError,
    PersistenceError,
    RecordNotFoundError,
)
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
    ``MemoryError`` and ``RecursionError`` are re-raised so process-level
    failures still surface; every other ``Exception`` falls back to a
    fresh correlation ID with a warning so operators can correlate the
    fallback to its triggering request.

    Returns:
        Resulting string.
    """
    try:
        ctx = structlog.contextvars.get_contextvars()
        request_id = ctx.get("request_id")
        if isinstance(request_id, str) and request_id:
            return request_id
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_CORRELATION_FALLBACK,
            error_type=type(exc).__qualname__,
            error=safe_error_description(exc),
        )
    return generate_correlation_id()


def _wants_problem_json(request: Request[object, object, State]) -> bool:
    """Check whether the client prefers ``application/problem+json``.

    Returns ``True`` only when the Accept header explicitly prefers
    ``application/problem+json`` over ``application/json``.  Defaults
    to ``False`` for ``*/*``, missing, or empty Accept headers.

    Wrapped defensively because this runs inside exception handlers,
    which are the last line of defense and must never crash.
    ``MemoryError`` and ``RecursionError`` are re-raised so process-level
    failures still surface; every other ``Exception`` falls back to the
    envelope format with a warning so a malformed Accept header from a
    misbehaving client cannot crash the response path.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    try:
        match = request.accept.best_match(
            ["application/json", _PROBLEM_JSON],
        )
    except Exception as exc:
        reraise_critical(exc)
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

    Returns:
        ``ApiResponse[None]`` instance.
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
    """Build a bare RFC 9457 ``application/problem+json`` response.

    Returns:
        ``Response[ProblemDetail]`` instance.
    """
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
    request: Request[object, object, State],
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

    Returns:
        ``Response[ApiResponse[None]] | Response[ProblemDetail]`` instance.
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
    except Exception as exc:
        reraise_critical(exc)
        # Last-resort fallback when structured-response construction
        # itself fails (e.g. Pydantic validation error from a corrupted
        # ErrorCode, structlog context corruption, enum drift).  Emit a
        # minimal but valid RFC 9457 body so client SDKs that decode
        # ``error_detail`` fields do not crash on null access.  The
        # negotiated content type is preserved -- ``application/json``
        # clients still receive the standard ``ApiResponse`` envelope
        # shape, only ``application/problem+json`` clients see a bare
        # ``ProblemDetail``.
        logger.error(
            API_REQUEST_ERROR,
            error_type="response_build_failure",
            error="Failed to build structured error response",
            detail=detail,
            original_status_code=status_code,
        )
        # Re-check content negotiation defensively: if ``_wants_problem_json``
        # itself was the original failure, default to the envelope shape
        # so the fallback never repeats the same crash.
        try:
            use_problem_json = _wants_problem_json(request)
        except Exception as exc:  # pragma: no cover
            reraise_critical(exc)
            use_problem_json = False
        instance = _get_instance_id()
        fallback_title = category_title(ErrorCategory.INTERNAL)
        fallback_type = category_type_uri(ErrorCategory.INTERNAL)
        if use_problem_json:
            return Response(
                content=ProblemDetail(
                    type=fallback_type,
                    title=fallback_title,
                    status=500,
                    detail="Internal server error",
                    instance=instance,
                    error_code=ErrorCode.INTERNAL_ERROR,
                    error_category=ErrorCategory.INTERNAL,
                    retryable=False,
                    retry_after=None,
                ),
                status_code=500,
                media_type=_PROBLEM_JSON,
            )
        return Response(
            content=ApiResponse[None](
                error="Internal server error",
                error_detail=ErrorDetail(
                    detail="Internal server error",
                    error_code=ErrorCode.INTERNAL_ERROR,
                    error_category=ErrorCategory.INTERNAL,
                    retryable=False,
                    retry_after=None,
                    instance=instance,
                    title=fallback_title,
                    type=fallback_type,
                ),
            ),
            status_code=500,
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
    """Map HTTP status to error code, category, and retryability.

    Returns:
        Tuple matching the ``tuple[ErrorCode, ErrorCategory, bool]`` annotation.
    """
    if status in _STATUS_TO_ERROR_META:
        return _STATUS_TO_ERROR_META[status]
    if status >= _SERVER_ERROR_THRESHOLD:
        return _SERVER_ERROR_DEFAULT
    return _CLIENT_ERROR_DEFAULT


_RESERVED_LOG_KEYS: Final = frozenset(
    {"method", "path", "status_code", "error_type", "error"},
)
_MAX_LOG_STR_LEN: Final = 256
_MAX_LOG_TUPLE_LEN: Final = 16


def _safe_log_attrs(exc: Exception) -> dict[str, object]:
    """Return primitive exception attributes safe to surface in logs.

    Domain error subclasses (e.g. ``WorkflowDefinitionRevisionMismatchError``,
    ``SubworkflowHasParentsError``) carry structured fields like
    ``definition_id``, ``expected``, ``actual``, ``subworkflow_id``,
    ``parent_ids`` that operators query log streams by. Without this
    pass-through they would be lost when controllers stop building
    their own log calls.

    Only primitives (``int`` / ``float`` / ``str`` / ``bool`` /
    ``None``) and small tuples of primitives are included; complex
    values (Pydantic models, nested dicts, custom objects) are skipped
    to keep credentials and connection objects out of the sink. Domain
    errors that need structured detail in logs expose a primitive-only
    representation alongside the rich one (e.g.
    ``SubworkflowHasParentsError`` exposes ``parent_ids: tuple[str,
    ...]`` next to the full ``parents: tuple[ParentReference, ...]``).
    Private (``_*``) attributes, dunder attributes, and the standard
    ``BaseException.args`` slot are skipped. Strings are clamped at
    ``_MAX_LOG_STR_LEN`` characters and tuples at
    ``_MAX_LOG_TUPLE_LEN`` elements. Keys colliding with the reserved
    log envelope (``method`` / ``path`` / ``status_code`` etc.) are
    dropped so the API request fields cannot be shadowed by an
    exception attribute.

    Returns:
        Mapping matching the ``dict[str, object]`` annotation.
    """
    safe: dict[str, object] = {}
    instance_attrs = getattr(exc, "__dict__", {})
    if not isinstance(instance_attrs, dict):
        return safe
    for name, value in instance_attrs.items():
        if name.startswith("_") or name == "args" or name in _RESERVED_LOG_KEYS:
            continue
        clamped = _clamp_log_value(value)
        if clamped is not _LOG_SKIP:
            safe[name] = clamped
    return safe


_LOG_SKIP: Final = object()


def _clamp_log_value(value: object) -> object:
    """Coerce *value* into a log-safe form, or ``_LOG_SKIP`` if unsafe.

    Returns:
        ``object`` instance.
    """
    match value:
        case None | True | False:
            return value
        case int() | float():
            return value
        case str():
            return value[:_MAX_LOG_STR_LEN]
        case tuple() if len(value) <= _MAX_LOG_TUPLE_LEN:
            items: list[object] = []
            for item in value:
                inner = _clamp_log_value(item)
                if inner is _LOG_SKIP:
                    return _LOG_SKIP
                items.append(inner)
            return tuple(items)
        case _:
            return _LOG_SKIP


def _log_error(
    request: Request[object, object, State],
    exc: Exception,
    *,
    status: int,
) -> None:
    """Log an API error with request context.

    Uses ERROR level for 5xx server errors and WARNING for 4xx client
    errors. ``error_type`` + ``error=safe_error_description(exc)``
    supplies operator-visible diagnostic context without attaching the
    traceback (whose frame-locals would carry connection strings,
    tokens, etc. straight to the sink). Domain-error structured
    attributes (e.g. ``definition_id``, ``expected`` revision) are
    surfaced via ``_safe_log_attrs`` so log queries by domain identifier
    keep working after controllers stop building per-error log calls.
    """
    log = logger.error if status >= _SERVER_ERROR_THRESHOLD else logger.warning
    log(
        API_REQUEST_ERROR,
        method=request.method,
        path=str(request.url.path),
        status_code=status,
        error_type=type(exc).__qualname__,
        error=safe_error_description(exc),
        **_safe_log_attrs(exc),
    )


def handle_record_not_found(
    request: Request[object, object, State],
    exc: RecordNotFoundError,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``RecordNotFoundError`` to 404.

    Returns:
        ``Response[ApiResponse[None]] | Response[ProblemDetail]`` instance.
    """
    _log_error(request, exc, status=404)
    return _build_response(
        request,
        detail="Resource not found",
        error_code=ErrorCode.RECORD_NOT_FOUND,
        error_category=ErrorCategory.NOT_FOUND,
        status_code=404,
    )


def handle_duplicate_record(
    request: Request[object, object, State],
    exc: DuplicateRecordError,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``DuplicateRecordError`` to 409.

    Returns:
        ``Response[ApiResponse[None]] | Response[ProblemDetail]`` instance.
    """
    _log_error(request, exc, status=409)
    return _build_response(
        request,
        detail="Resource already exists",
        error_code=ErrorCode.DUPLICATE_RECORD,
        error_category=ErrorCategory.CONFLICT,
        status_code=409,
    )


def handle_persistence_error(
    request: Request[object, object, State],
    exc: PersistenceError,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``PersistenceError`` to 500.

    Returns:
        ``Response[ApiResponse[None]] | Response[ProblemDetail]`` instance.
    """
    _log_error(request, exc, status=500)
    return _build_response(
        request,
        detail="Internal server error",
        error_code=ErrorCode.PERSISTENCE_ERROR,
        error_category=ErrorCategory.INTERNAL,
        status_code=500,
    )


def handle_persistence_integrity_error(
    request: Request[object, object, State],
    exc: Exception,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``psycopg.errors.IntegrityError`` (and subclasses) to 400.

    Foreign-key, unique, and not-null violations raised by the
    underlying driver indicate that the request body referenced a
    row that does not exist (or violates a uniqueness constraint).
    These are caller errors, not server errors -- 400 with a
    structured body is the honest mapping.

    Domain code is expected to validate-first and raise typed
    domain errors (``ConnectionNotFoundError`` / ``ValidationError``)
    so this handler is a backstop, not the primary path. Logging
    via the standard helper keeps any embedded SQL fragments out
    of the audit trail.

    Returns:
        ``Response[ApiResponse[None]] | Response[ProblemDetail]`` instance.
    """
    _log_error(request, exc, status=400)
    return _build_response(
        request,
        detail="persistence integrity violation",
        error_code=ErrorCode.VALIDATION_ERROR,
        error_category=ErrorCategory.VALIDATION,
        status_code=400,
    )


def _normalize_status_code(raw: object) -> int:
    """Coerce a raw ``status_code`` attribute to a valid HTTP error code.

    A non-int attribute or a value outside the 400-599 error range is
    mis-annotation territory; normalize to 500 so the handler cannot
    produce a "successful" error envelope.  A warning is logged on
    every normalization so a typo'd ``status_code`` field on an
    exception class surfaces in operator logs instead of silently
    rendering as 500.

    Returns:
        Resulting integer.
    """
    value: int
    try:
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            logger.warning(
                API_REQUEST_ERROR,
                error_type="status_code_invalid_type",
                raw_value_type=type(raw).__qualname__,
                raw_value=repr(raw)[:100],
            )
            return 500
        value = int(raw)
    except (TypeError, ValueError) as exc:
        logger.warning(
            API_REQUEST_ERROR,
            error_type="status_code_coercion_failed",
            raw_value=repr(raw)[:100],
            error=safe_error_description(exc),
        )
        return 500
    if not (400 <= value <= 599):  # noqa: PLR2004
        logger.warning(
            API_REQUEST_ERROR,
            error_type="status_code_out_of_range",
            raw_value=value,
        )
        return 500
    return value


def _normalize_error_metadata(
    exc: Exception,
) -> tuple[ErrorCode, ErrorCategory]:
    """Return validated ``(error_code, error_category)`` for ``exc``.

    Values that are not members of their respective enums are replaced
    by the generic INTERNAL fallbacks so ``_build_response`` never
    serialises junk.

    Returns:
        Tuple matching the ``tuple[ErrorCode, ErrorCategory]`` annotation.
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

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    sentinel = object()
    is_retryable = getattr(exc, "is_retryable", sentinel)
    if is_retryable is not sentinel:
        return bool(is_retryable)
    return bool(getattr(exc, "retryable", False))


def _select_message(exc: Exception, status_code: int) -> str:
    """Pick a user-safe message for the RFC 9457 envelope.

    5xx responses return the class-level ``default_message`` to avoid
    leaking internal detail; 4xx responses pass through the exception
    message (controller-authored, user-safe).

    Returns:
        Resulting string.
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

    Returns:
        The ``int`` value when present, ``None`` otherwise.
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
    request: Request[object, object, State],
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

    Returns:
        ``Response[ApiResponse[None]] | Response[ProblemDetail]`` instance.
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
    request: Request[object, object, State],
    exc: Exception,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Catch-all for unexpected errors -> 500.

    Returns:
        ``Response[ApiResponse[None]] | Response[ProblemDetail]`` instance.
    """
    _log_error(request, exc, status=500)
    return _build_response(
        request,
        detail="Internal server error",
        error_code=ErrorCode.INTERNAL_ERROR,
        error_category=ErrorCategory.INTERNAL,
        status_code=500,
    )


def handle_permission_denied(
    request: Request[object, object, State],
    exc: PermissionDeniedException,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``PermissionDeniedException`` to 403.

    Returns:
        ``Response[ApiResponse[None]] | Response[ProblemDetail]`` instance.
    """
    _log_error(request, exc, status=403)
    return _build_response(
        request,
        detail="Forbidden",
        error_code=ErrorCode.FORBIDDEN,
        error_category=ErrorCategory.AUTH,
        status_code=403,
    )


def handle_validation_error(
    request: Request[object, object, State],
    exc: ValidationException,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``ValidationException`` to 400.

    Returns:
        ``Response[ApiResponse[None]] | Response[ProblemDetail]`` instance.
    """
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
    request: Request[object, object, State],
    exc: InvalidCursorError,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map :class:`InvalidCursorError` to 400.

    Cursor tokens are opaque to the client; if tampering or decoding
    fails, surface a sanitised description (via
    ``safe_error_description``) in the 400 response body so operators
    can distinguish malformed-base64 from signature-mismatch without
    leaking secret-prefixed tokens or signature material into the
    error envelope.

    Returns:
        ``Response[ApiResponse[None]] | Response[ProblemDetail]`` instance.
    """
    _log_error(request, exc, status=400)
    # ``safe_error_description`` is documented to always return at
    # least ``type(exc).__name__`` when ``str(exc)`` is empty, so no
    # fallback string is needed here.
    detail = safe_error_description(exc)
    return _build_response(
        request,
        detail=detail,
        error_code=ErrorCode.REQUEST_VALIDATION_ERROR,
        error_category=ErrorCategory.VALIDATION,
        status_code=400,
    )


def handle_not_authorized(
    request: Request[object, object, State],
    exc: NotAuthorizedException,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map ``NotAuthorizedException`` to 401 with a discriminated error_code.

    Reads ``exc.detail`` to distinguish "no session token" (fresh page
    load) from "expired session" so the dashboard can choose between a
    silent redirect to /login (no_token) and a redirect plus
    informational toast (expired). The auth middleware in
    ``synthorg.api.auth.middleware`` is the only producer of these
    detail strings; see :func:`discriminate_unauthorized` in
    :mod:`synthorg.api.auth_response_discriminator` for the mapping.

    Returns:
        ``Response[ApiResponse[None]] | Response[ProblemDetail]`` instance.
    """
    _log_error(request, exc, status=401)
    error_code, detail = discriminate_unauthorized(exc.detail)
    return _build_response(
        request,
        detail=detail,
        error_code=error_code,
        error_category=ErrorCategory.AUTH,
        status_code=401,
    )


def handle_not_found(
    request: Request[object, object, State],
    exc: NotFoundException,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Map Litestar ``NotFoundException`` to 404.

    Ensures unmatched routes return 404 instead of falling through
    to ``handle_unexpected`` (which returns 500), which ZAP flags
    as a security misconfiguration.

    Returns:
        ``Response[ApiResponse[None]] | Response[ProblemDetail]`` instance.
    """
    logger.warning(
        API_ROUTE_NOT_FOUND,
        method=request.method,
        path=str(request.url.path),
        status_code=404,
        error_type=type(exc).__qualname__,
        error=safe_error_description(exc),
    )
    return _build_response(
        request,
        detail="Not found",
        error_code=ErrorCode.ROUTE_NOT_FOUND,
        error_category=ErrorCategory.NOT_FOUND,
        status_code=404,
    )


def handle_http_exception(
    request: Request[object, object, State],
    exc: HTTPException,
) -> Response[ApiResponse[None]] | Response[ProblemDetail]:
    """Catch-all for unhandled Litestar ``HTTPException`` subclasses.

    Preserves the correct status code (e.g. 405, 429) instead of
    letting them fall through to ``handle_unexpected`` as 500.

    Returns:
        ``Response[ApiResponse[None]] | Response[ProblemDetail]`` instance.
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
        # ``exc.detail`` is typed as ``str | None`` in Litestar but
        # third-party HTTPException subclasses occasionally set it to
        # bytes or a non-string sequence; coerce so the slice always
        # returns a str rather than the same type as ``detail``.
        raw_detail = exc.detail or fallback
        msg = (raw_detail if isinstance(raw_detail, str) else str(raw_detail))[
            :_MAX_DETAIL_LEN
        ]
    code, category, retryable = _category_for_status(status)
    # Parse Retry-After header into the body field for agent consumers.
    retry_after: int | None = None
    raw_headers = exc.headers or {}
    raw_retry = raw_headers.get("Retry-After") or raw_headers.get("retry-after")
    if raw_retry:
        try:
            retry_after = int(raw_retry)
        except ValueError:
            # Malformed Retry-After header from upstream is rare but
            # observable; surfacing it lets operators distinguish a real
            # missing header from a misbehaving upstream service.
            logger.warning(
                API_REQUEST_ERROR,
                error_type="retry_after_parse_error",
                raw_retry_after=raw_retry,
                path=str(request.url.path),
            )
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


# Persistence-layer integrity violations (FK / unique / not-null /
# generic constraint failures) translate into ``ConstraintViolationError``
# inside the repository modules; the api layer catches that domain
# class instead of importing the psycopg driver directly so the HTTP
# layer stays decoupled from the persistence backend choice. Per the
# project persistence-boundary rule, ``psycopg`` may only be imported
# from ``src/synthorg/persistence/``; the previous direct import here
# was a sanctioned exception kept while the driver-translation path
# was incomplete -- now that ``ConstraintViolationError`` is the
# established domain mapping (see
# ``synthorg.persistence.postgres.approval_repo`` for the canonical
# translation pattern), the api layer registers the domain class and
# the driver import is no longer needed.
_HANDLER_ENTRIES: tuple[tuple[type[Exception], object], ...] = (
    (RecordNotFoundError, handle_record_not_found),
    (DuplicateRecordError, handle_duplicate_record),
    (ConstraintViolationError, handle_persistence_integrity_error),
    (PersistenceError, handle_persistence_error),
    (NotAuthorizedException, handle_not_authorized),
    (PermissionDeniedException, handle_permission_denied),
    (ValidationException, handle_validation_error),
    (InvalidCursorError, handle_invalid_cursor),
    (NotFoundException, handle_not_found),
    (HTTPException, handle_http_exception),
    # Domain error hierarchies -- MRO dispatch covers every subclass.
    # ``RateLimitError`` is listed explicitly so its narrower 429
    # status takes precedence over the ``ProviderError`` (502) default
    # when Litestar walks the raised exception's MRO.
    (RateLimitError, handle_domain_error),
    (EngineError, handle_domain_error),
    (BudgetExhaustedError, handle_domain_error),
    (MixedCurrencyAggregationError, handle_domain_error),
    (ProviderError, handle_domain_error),
    (OntologyError, handle_domain_error),
    (CommunicationError, handle_domain_error),
    (IntegrationError, handle_domain_error),
    (ToolError, handle_domain_error),
    (A2AClientError, handle_domain_error),
    (DomainError, handle_domain_error),
    (Exception, handle_unexpected),
)


# Litestar resolves exception handlers by walking the raised exception's
# MRO and picks the first matching type, so dict insertion order does
# not affect resolution priority. ``HTTPException`` integer status-code
# keys are resolved separately by Litestar; this table uses only type
# keys. ``ConstraintViolationError`` is registered above
# ``PersistenceError`` (its parent via ``QueryError``) so the
# narrower 400 mapping wins for FK / unique violations -- if it were
# below, MRO would still pick the first match.
EXCEPTION_HANDLERS: MappingProxyType[type[Exception], object] = MappingProxyType(
    {
        exc_type: handler
        for exc_type, handler in _HANDLER_ENTRIES
        if exc_type is not None
    }
)
