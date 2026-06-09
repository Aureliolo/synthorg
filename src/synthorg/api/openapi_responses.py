# module-kind: code
"""Reusable RFC 9457 error responses for the OpenAPI post-processor.

Defines the dual-format (envelope + ``application/problem+json``) error
response catalogue, the per-operation injection policy
(``_should_inject``), and the ``ProblemDetail`` schema builder. Consumed
by ``inject_rfc9457_responses`` in ``synthorg.api.openapi``.
"""

from typing import Final, NamedTuple, cast

from pydantic import JsonValue

from synthorg.api.dto import ProblemDetail
from synthorg.core.error_taxonomy import (
    CATEGORY_TITLES,
    ErrorCategory,
    ErrorCode,
    category_type_uri,
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_OPENAPI_SCHEMA_ENHANCED

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────

_PROBLEM_JSON: Final[str] = "application/problem+json"
_APP_JSON: Final[str] = "application/json"

# Paths that skip authentication (no 401/403 injected).
_PUBLIC_PATH_SUFFIXES: Final[tuple[str, ...]] = (
    "/healthz",
    "/readyz",
    "/auth/setup",
    "/auth/login",
    "/setup/status",
)

# HTTP methods that mutate state.  Includes DELETE for 400/403 injection;
# DELETE is intentionally excluded from 409 by the Conflict injection logic.
_WRITE_METHODS: Final[frozenset[str]] = frozenset(
    {"post", "put", "patch", "delete"},
)

# Envelope schema ref (Litestar-generated name for ApiResponse[None]).
_ENVELOPE_REF: Final[str] = "#/components/schemas/ApiResponse_NoneType_"

# ProblemDetail schema ref (we add this).
_PROBLEM_DETAIL_REF: Final[str] = "#/components/schemas/ProblemDetail"

_EXAMPLE_INSTANCE_ID: Final[str] = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

# ── Error response definitions ────────────────────────────────


class _ErrorResponseSpec(NamedTuple):
    """Specification for a reusable error response definition."""

    status: int
    key: str
    description: str
    error_code: ErrorCode
    error_category: ErrorCategory
    detail: str
    retryable: bool


_ERROR_RESPONSES: Final[tuple[_ErrorResponseSpec, ...]] = (
    _ErrorResponseSpec(
        status=400,
        key="BadRequest",
        description="Validation error -- request body or parameters are invalid.",
        error_code=ErrorCode.REQUEST_VALIDATION_ERROR,
        error_category=ErrorCategory.VALIDATION,
        detail="Validation error",
        retryable=False,
    ),
    _ErrorResponseSpec(
        status=401,
        key="Unauthorized",
        description="Authentication required -- missing or invalid credentials.",
        error_code=ErrorCode.UNAUTHORIZED,
        error_category=ErrorCategory.AUTH,
        detail="Authentication required",
        retryable=False,
    ),
    _ErrorResponseSpec(
        status=403,
        key="Forbidden",
        description="Insufficient permissions for this operation.",
        error_code=ErrorCode.FORBIDDEN,
        error_category=ErrorCategory.AUTH,
        detail="Forbidden",
        retryable=False,
    ),
    _ErrorResponseSpec(
        status=404,
        key="NotFound",
        description="Requested resource does not exist.",
        error_code=ErrorCode.RECORD_NOT_FOUND,
        error_category=ErrorCategory.NOT_FOUND,
        detail="Resource not found",
        retryable=False,
    ),
    _ErrorResponseSpec(
        status=409,
        key="Conflict",
        description="Resource conflict -- duplicate or invalid state transition.",
        error_code=ErrorCode.RESOURCE_CONFLICT,
        error_category=ErrorCategory.CONFLICT,
        detail="Resource conflict",
        retryable=False,
    ),
    _ErrorResponseSpec(
        status=429,
        key="TooManyRequests",
        description="Rate limit exceeded -- back off and retry.",
        error_code=ErrorCode.RATE_LIMITED,
        error_category=ErrorCategory.RATE_LIMIT,
        detail="Rate limit exceeded",
        retryable=True,
    ),
    _ErrorResponseSpec(
        status=500,
        key="InternalError",
        description="Internal server error.",
        error_code=ErrorCode.INTERNAL_ERROR,
        error_category=ErrorCategory.INTERNAL,
        detail="Internal server error",
        retryable=False,
    ),
    _ErrorResponseSpec(
        status=503,
        key="ServiceUnavailable",
        description="Required service is temporarily unavailable.",
        error_code=ErrorCode.SERVICE_UNAVAILABLE,
        error_category=ErrorCategory.INTERNAL,
        detail="Service unavailable",
        retryable=True,
    ),
)

_RFC9457_DESCRIPTION_SECTION: Final[str] = """\
## Error Handling (RFC 9457)

All error responses support content negotiation between two formats:

- **`application/json`** (default): Standard `ApiResponse` envelope with \
`error`, `error_detail`, and `success` fields
- **`application/problem+json`**: Bare RFC 9457 Problem Detail body -- \
send `Accept: application/problem+json`

Every error includes machine-readable metadata: `error_code` \
(4-digit category-grouped), `error_category`, `retryable`, and \
`retry_after` (seconds).

See the [Error Reference](https://synthorg.io/docs/errors) for the \
full error taxonomy and retry guidance.\
"""


# ── Helpers ───────────────────────────────────────────────────


def _build_problem_detail_schema() -> dict[str, JsonValue]:
    """Generate the ``ProblemDetail`` JSON Schema from the Pydantic model.

    Rewrites internal ``$defs`` references to point at
    ``#/components/schemas/`` so they resolve correctly when placed
    inside the OpenAPI ``components.schemas`` section.

    .. note::

        The ``$defs`` block is stripped because the referenced schemas
        (e.g. ``ErrorCode``, ``ErrorCategory``) are already present in
        the Litestar-generated ``components.schemas``.  If this function
        is used with a schema that lacks those definitions, the rewritten
        ``$ref`` paths will be dangling.

    Returns:
        JSON Schema dict for ``ProblemDetail`` with ``$defs``
        stripped and ``$ref`` paths rewritten to resolve under
        ``#/components/schemas/``.
    """
    raw = ProblemDetail.model_json_schema(mode="serialization")

    # Strip $defs -- referenced types already exist in components.schemas.
    raw.pop("$defs", None)

    # Rewrite $ref from '#/$defs/X' to '#/components/schemas/X'. A dict input
    # yields a dict output, so the cast is sound at this boundary.
    return cast("dict[str, JsonValue]", _rewrite_refs(raw))


def _rewrite_refs(obj: JsonValue) -> JsonValue:
    """Recursively rewrite ``$ref`` paths from Pydantic to OpenAPI.

    Only rewrites ``#/$defs/``-prefixed refs to
    ``#/components/schemas/``.  Other prefixes (e.g.
    already-rewritten ``#/components/schemas/``) pass through
    unchanged for idempotency.

    Returns:
        ``Any`` instance.
    """
    if isinstance(obj, dict):
        ref = obj.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return {
                "$ref": (f"#/components/schemas/{ref.removeprefix('#/$defs/')}"),
            }
        return {k: _rewrite_refs(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rewrite_refs(item) for item in obj]
    return obj


def _envelope_example(
    *,
    detail: str,
    error_code: ErrorCode,
    error_category: ErrorCategory,
    retryable: bool,
) -> dict[str, JsonValue]:
    """Build an ``ApiResponse`` envelope example for an error response.

    Returns:
        Mapping with the declared key/value types.
    """
    title = CATEGORY_TITLES[error_category]
    type_uri = category_type_uri(error_category)
    return {
        "data": None,
        "error": detail,
        "error_detail": {
            "detail": detail,
            "error_code": error_code.value,
            "error_category": error_category.value,
            "retryable": retryable,
            "retry_after": None,
            "instance": _EXAMPLE_INSTANCE_ID,
            "title": title,
            "type": type_uri,
        },
        "success": False,
    }


def _problem_detail_example(
    *,
    status: int,
    detail: str,
    error_code: ErrorCode,
    error_category: ErrorCategory,
    retryable: bool,
) -> dict[str, JsonValue]:
    """Build a bare RFC 9457 ``ProblemDetail`` example.

    Returns:
        Mapping with the declared key/value types.
    """
    title = CATEGORY_TITLES[error_category]
    type_uri = category_type_uri(error_category)
    return {
        "type": type_uri,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": _EXAMPLE_INSTANCE_ID,
        "error_code": error_code.value,
        "error_category": error_category.value,
        "retryable": retryable,
        "retry_after": None,
    }


def _build_reusable_response(spec: _ErrorResponseSpec) -> dict[str, JsonValue]:
    """Build a reusable response object with dual content types.

    Returns:
        Mapping with the declared key/value types.
    """
    return {
        "description": spec.description,
        "content": {
            _APP_JSON: {
                "schema": {"$ref": _ENVELOPE_REF},
                "example": _envelope_example(
                    detail=spec.detail,
                    error_code=spec.error_code,
                    error_category=spec.error_category,
                    retryable=spec.retryable,
                ),
            },
            _PROBLEM_JSON: {
                "schema": {"$ref": _PROBLEM_DETAIL_REF},
                "example": _problem_detail_example(
                    status=spec.status,
                    detail=spec.detail,
                    error_code=spec.error_code,
                    error_category=spec.error_category,
                    retryable=spec.retryable,
                ),
            },
        },
    }


def _is_public_path(path: str) -> bool:
    """Check whether a path is unauthenticated (no 401/403).

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    return any(path.endswith(suffix) for suffix in _PUBLIC_PATH_SUFFIXES)


def _has_path_params(path: str) -> bool:
    """Check whether a path contains ``{param}`` segments.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    return "{" in path


def _response_ref(key: str) -> dict[str, JsonValue]:
    """Build a ``$ref`` to a reusable response.

    Returns:
        Mapping with the declared key/value types.
    """
    return {"$ref": f"#/components/responses/{key}"}


# ── Response-to-operation mapping ─────────────────────────────


def _has_response_code(operation: dict[str, JsonValue], code: str) -> bool:
    """Report whether *operation* already declares a response for *code*.

    Returns:
        ``True`` when the operation's ``responses`` map contains *code*.
    """
    responses = operation.get("responses")
    return isinstance(responses, dict) and code in responses


def _should_inject(
    key: str,
    path: str,
    method: str,
    operation: dict[str, JsonValue],
) -> bool:
    """Decide whether to inject a response reference into an operation.

    Returns ``True`` when the given error response *key* is applicable
    to the *path*/*method* combination.  Returns ``False`` for
    unrecognised keys (defensive fallback).

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    is_public = _is_public_path(path)
    is_write = method in _WRITE_METHODS
    has_params = _has_path_params(path)

    # ``/readyz`` is the one public path that legitimately returns
    # 503 when persistence / message-bus subsystems are unhealthy,
    # so the schema MUST document it even though the path is public.
    # Other public paths (``/healthz``, ``/auth/*``, ``/setup/status``)
    # always return 200 while the process is alive.
    serves_503 = path.endswith("/readyz")

    checks: dict[str, bool] = {
        "InternalError": True,
        "ServiceUnavailable": not is_public or serves_503,
        "Unauthorized": not is_public,
        "Forbidden": not is_public and is_write,
        # Inject on write methods or replace Litestar's incorrect default.
        "BadRequest": is_write or _has_response_code(operation, "400"),
        "NotFound": has_params,
        # DELETE excluded -- this API's deletes are unconditional removals
        # with no state preconditions, so 409 Conflict does not apply.
        "Conflict": is_write and method != "delete",
        "TooManyRequests": not is_public,
    }
    return checks.get(key, False)


def _is_litestar_validation_400(response: dict[str, JsonValue]) -> bool:
    """Detect Litestar's auto-generated ``ValidationException`` 400 response.

    Returns ``True`` when the response schema contains the
    ``"Validation Exception"`` description that Litestar emits for
    request-body validation errors.  Custom 400 responses will not
    match this heuristic and are left untouched.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    content = response.get("content")
    if not isinstance(content, dict):
        return False
    json_content = content.get("application/json")
    if not isinstance(json_content, dict):
        return False
    schema = json_content.get("schema")
    if not isinstance(schema, dict):
        return False
    return str(schema.get("description", "")) == "Validation Exception"


def _inject_operation_responses(
    paths: dict[str, JsonValue],
    response_keys: list[str],
    status_for_key: dict[str, str],
) -> None:
    """Inject error response refs into each operation in *paths*.

    Mutates *paths* in place -- the caller is responsible for
    passing a deep-copied schema.
    """
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            op_responses = operation["responses"]
            if not isinstance(op_responses, dict):
                continue
            for key in response_keys:
                status_code = status_for_key[key]
                if not _should_inject(key, path, method, operation):
                    continue
                if status_code == "400":
                    # Only replace Litestar's auto-generated
                    # ValidationException 400; preserve custom 400s.
                    existing = op_responses.get("400")
                    if existing is None or (
                        isinstance(existing, dict)
                        and _is_litestar_validation_400(existing)
                    ):
                        op_responses["400"] = _response_ref(key)
                elif status_code not in op_responses:
                    op_responses[status_code] = _response_ref(key)


# ── Extracted steps ───────────────────────────────────────────


def _add_problem_detail_schema(schemas: dict[str, JsonValue]) -> None:
    """Add ``ProblemDetail`` to ``components.schemas`` if absent."""
    if "ProblemDetail" not in schemas:
        schemas["ProblemDetail"] = _build_problem_detail_schema()
        logger.debug(
            API_OPENAPI_SCHEMA_ENHANCED,
            step="add_problem_detail",
            added=True,
        )
    else:
        logger.debug(
            API_OPENAPI_SCHEMA_ENHANCED,
            step="add_problem_detail",
            added=False,
            reason="already_exists",
        )


def _build_all_responses(
    responses: dict[str, JsonValue],
) -> tuple[list[str], dict[str, str]]:
    """Build reusable error responses and return keys + status mapping.

    Returns:
        Tuple of the declared element types.
    """
    response_keys: list[str] = []
    status_for_key: dict[str, str] = {}
    for spec in _ERROR_RESPONSES:
        responses[spec.key] = _build_reusable_response(spec)
        response_keys.append(spec.key)
        status_for_key[spec.key] = str(spec.status)
    return response_keys, status_for_key


def _update_info_description(info: dict[str, JsonValue]) -> None:
    """Store RFC 9457 documentation in an extension field.

    Uses ``x-documentation`` so the content is preserved in the
    spec but not rendered inline by API doc renderers (which displays
    ``info.description`` prominently at the top of the page).
    """
    x_doc = info.get("x-documentation")
    if not isinstance(x_doc, dict):
        x_doc = {}
        info["x-documentation"] = x_doc
    x_doc.setdefault("rfc9457", _RFC9457_DESCRIPTION_SECTION)
