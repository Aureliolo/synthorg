"""OpenAPI schema post-processor for RFC 9457 dual-format error responses.

Litestar auto-generates the OpenAPI schema from controller return types,
but exception handlers (which perform content negotiation between
``application/json`` envelopes and ``application/problem+json`` bare
bodies) are invisible to the generator.

This module provides :func:`inject_rfc9457_responses` which transforms
the Litestar-generated schema dict to:

1. Flatten nullable ``oneOf`` unions to JSON Schema 2020-12 ``type``
   arrays (fixes API doc renderers *"Expected union value"* warnings)
2. Add the ``ProblemDetail`` schema (RFC 9457 bare response body)
3. Define reusable error responses with dual content types
4. Inject error response references into every operation
5. Replace Litestar's default 400 schema with the actual envelope
6. Store content negotiation docs in ``info.x-documentation``

Called by ``scripts/export_openapi.py`` after schema generation.

.. note::

    The ``ProblemDetail`` schema rewrites ``$ref`` paths from Pydantic's
    internal ``#/$defs/`` to ``#/components/schemas/``.  This assumes
    the referenced schemas (``ErrorCode``, ``ErrorCategory``) already
    exist in the Litestar-generated ``components.schemas``.
"""

import copy
from typing import Any, Final, NamedTuple

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


# ── Nullable union normalization ──────────────────────────────
#
# The helpers below mutate ``result`` (a freshly constructed dict from
# the enclosing comprehension in ``_normalize_nullable_unions``) in
# place.  They must not be called on the original input schema;
# ``inject_rfc9457_responses`` deep-copies it first.

_SCHEMAS_PREFIX: Final[str] = "#/components/schemas/"


def _flatten_nullable_ref(
    result: dict[str, Any],
    keyword: str,
    branch: dict[str, Any],
    all_schemas: dict[str, Any],
) -> bool:
    """Inline a nullable ``$ref`` to an enum schema.

    When the ``$ref`` target is a simple enum (has ``type`` and
    ``enum``), inlines the enum values and flattens to
    ``{type: [T, "null"], enum: [..., null]}``.

    Returns ``True`` if the union was handled, ``False`` otherwise.
    """
    ref: str = branch.get("$ref", "")
    if not ref.startswith(_SCHEMAS_PREFIX):
        return False

    target_name = ref.removeprefix(_SCHEMAS_PREFIX)
    target = all_schemas.get(target_name, {})

    if "enum" not in target or "type" not in target:
        return False

    prop_desc = result.get("description")
    merged = {k: v for k, v in target.items() if k not in ("title", "description")}
    merged["type"] = [target["type"], "null"]
    merged["enum"] = [*target["enum"], None]
    del result[keyword]
    result.update(merged)
    if prop_desc:
        result["description"] = prop_desc
    return True


def _flatten_nullable(
    result: dict[str, Any],
    keyword: str,
    items: list[Any],
    all_schemas: dict[str, Any] | None = None,
) -> None:
    """Flatten a nullable union (``T | None``) in *result* in place.

    * Primitive branch (has ``type``): collapses to
      ``{type: [T, "null"], ...extras}``.
    * ``$ref`` to enum: delegates to :func:`_flatten_nullable_ref`.
    * Other ``$ref``: swaps ``oneOf`` to ``anyOf``.
    * Discriminated-union nullable (multiple ``$ref`` branches + null):
      swaps ``oneOf`` to ``anyOf`` so the null branch is tolerated.
    """
    null_entries = [i for i in items if isinstance(i, dict) and i.get("type") == "null"]
    if len(null_entries) != 1:
        return

    non_null = [i for i in items if i is not null_entries[0]]
    if not non_null:
        return

    # Multi-primitive nullable union (e.g. str | int | float | bool | None):
    # all non-null branches are primitive types -> collapse to type array.
    if len(non_null) > 1 and all(
        isinstance(b, dict) and "type" in b and len(b) == 1 for b in non_null
    ):
        types = [b["type"] for b in non_null] + ["null"]
        del result[keyword]
        result["type"] = types
        return

    if len(non_null) != 1:
        # Nullable multi-$ref union (typical shape for
        # ``Annotated[A | B, Field(discriminator=...)] | None`` -- Pydantic
        # emits ``oneOf: [$ref, $ref, ..., {type: "null"}]`` without the
        # ``"discriminator"`` marker surviving through Litestar schema
        # generation, so we cannot test for it directly).  Converting to
        # ``anyOf`` keeps every branch and tolerates null without losing
        # information: the underlying $ref schemas still carry their own
        # constraints, so validation against the union remains sound.
        # We intentionally do NOT touch non-``$ref`` multi-branch oneOfs
        # here -- those would represent genuinely exclusive primitive
        # unions that would be weakened by becoming ``anyOf``.
        if keyword == "oneOf" and all(
            isinstance(b, dict) and "$ref" in b for b in non_null
        ):
            result["anyOf"] = result.pop("oneOf")
        return

    branch = non_null[0]
    if isinstance(branch, dict) and "type" in branch:
        merged = {k: v for k, v in branch.items() if k != "type"}
        merged["type"] = [branch["type"], "null"]
        del result[keyword]
        result.update(merged)
        return

    if (
        isinstance(branch, dict)
        and "$ref" in branch
        and all_schemas
        and _flatten_nullable_ref(result, keyword, branch, all_schemas)
    ):
        return

    if keyword == "oneOf":
        result["anyOf"] = result.pop("oneOf")


_EXPECTED_UNION_BRANCHES: Final[int] = 2


def _collapse_redundant_union(
    result: dict[str, Any],
    keyword: str,
    items: list[Any],
) -> None:
    """Collapse a redundant ``oneOf`` with an empty schema.

    Litestar emits ``oneOf: [{$ref: ...}, {}]`` for tuple item
    schemas.  Only applies to ``oneOf`` -- collapsing ``anyOf``
    with ``{}`` would change semantics (``{}`` matches anything,
    so ``anyOf`` with ``{}`` means "accept anything").
    """
    if keyword != "oneOf" or len(items) != _EXPECTED_UNION_BRANCHES:
        return
    empty_entries = [i for i in items if isinstance(i, dict) and not i]
    if len(empty_entries) != 1:
        return
    concrete = [i for i in items if i is not empty_entries[0]]
    if concrete:
        del result[keyword]
        result.update(concrete[0])


def _normalize_nullable_unions(
    obj: Any,
    all_schemas: dict[str, Any] | None = None,
) -> Any:
    """Flatten nullable union schemas to idiomatic JSON Schema 2020-12.

    Litestar wraps ``T | None`` fields in ``oneOf``, producing
    ``oneOf: [{type: "string"}, {type: "null"}]``.  API doc renderers
    expect the compact ``type: ["string", "null"]`` form for
    primitives, and ``anyOf`` for ``$ref``-based nullables.

    Args:
        obj: Any JSON-serialisable value (typically the full OpenAPI
            schema dict).
        all_schemas: ``components.schemas`` dict used to resolve
            ``$ref`` targets for enum inlining.  When ``None``,
            ``$ref``-based nullable unions are converted to ``anyOf``
            (enums cannot be inlined without schema resolution).

    Conversion rules (applied to both ``oneOf`` and ``anyOf``):

    * **Primitive nullable** -- non-null branch has a ``type`` key:
      merge into ``{type: [T, "null"], ...extras}``.
    * **Enum $ref nullable** -- non-null branch is a ``$ref`` to a
      simple enum: inline the enum values and flatten.
    * **Object $ref nullable** -- non-null branch is a ``$ref`` to
      a complex schema: convert to ``anyOf`` (known renderer
      bug -- see linked issue for details).
    * **Redundant union** -- one branch is an empty schema ``{}``:
      collapse to just the non-empty branch (Litestar emits this
      for ``tuple[T, ...]`` item schemas).
    * **Discriminated unions** -- no ``{"type": "null"}`` entry and
      no empty-schema branch: left unchanged.
    """
    if isinstance(obj, dict):
        result = {k: _normalize_nullable_unions(v, all_schemas) for k, v in obj.items()}

        for keyword in ("oneOf", "anyOf"):
            if keyword not in result or not isinstance(result[keyword], list):
                continue
            _flatten_nullable(result, keyword, result[keyword], all_schemas)
            if keyword in result:
                # Re-fetch: _flatten_nullable may have replaced the list.
                _collapse_redundant_union(result, keyword, result[keyword])

        return result

    if isinstance(obj, list):
        return [_normalize_nullable_unions(item, all_schemas) for item in obj]
    return obj


# ── Helpers ───────────────────────────────────────────────────


def _build_problem_detail_schema() -> dict[str, Any]:
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

    # Rewrite $ref from '#/$defs/X' to '#/components/schemas/X'.
    result: dict[str, Any] = _rewrite_refs(raw)
    return result


def _rewrite_refs(obj: Any) -> Any:
    """Recursively rewrite ``$ref`` paths from Pydantic to OpenAPI.

    Only rewrites ``#/$defs/``-prefixed refs to
    ``#/components/schemas/``.  Other prefixes (e.g.
    already-rewritten ``#/components/schemas/``) pass through
    unchanged for idempotency.
    """
    if isinstance(obj, dict):
        if "$ref" in obj:
            ref: str = obj["$ref"]
            if ref.startswith("#/$defs/"):
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
) -> dict[str, Any]:
    """Build an ``ApiResponse`` envelope example for an error response."""
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
) -> dict[str, Any]:
    """Build a bare RFC 9457 ``ProblemDetail`` example."""
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


def _build_reusable_response(spec: _ErrorResponseSpec) -> dict[str, Any]:
    """Build a reusable response object with dual content types."""
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
    """Check whether a path is unauthenticated (no 401/403)."""
    return any(path.endswith(suffix) for suffix in _PUBLIC_PATH_SUFFIXES)


def _has_path_params(path: str) -> bool:
    """Check whether a path contains ``{param}`` segments."""
    return "{" in path


def _response_ref(key: str) -> dict[str, str]:
    """Build a ``$ref`` to a reusable response."""
    return {"$ref": f"#/components/responses/{key}"}


# ── Response-to-operation mapping ─────────────────────────────


def _should_inject(
    key: str,
    path: str,
    method: str,
    operation: dict[str, Any],
) -> bool:
    """Decide whether to inject a response reference into an operation.

    Returns ``True`` when the given error response *key* is applicable
    to the *path*/*method* combination.  Returns ``False`` for
    unrecognised keys (defensive fallback).
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
        "BadRequest": is_write or "400" in operation.get("responses", {}),
        "NotFound": has_params,
        # DELETE excluded -- this API's deletes are unconditional removals
        # with no state preconditions, so 409 Conflict does not apply.
        "Conflict": is_write and method != "delete",
        "TooManyRequests": not is_public,
    }
    return checks.get(key, False)


def _is_litestar_validation_400(response: dict[str, Any]) -> bool:
    """Detect Litestar's auto-generated ``ValidationException`` 400 response.

    Returns ``True`` when the response schema contains the
    ``"Validation Exception"`` description that Litestar emits for
    request-body validation errors.  Custom 400 responses will not
    match this heuristic and are left untouched.
    """
    content: dict[str, Any] = response.get("content", {})
    json_content: dict[str, Any] = content.get("application/json", {})
    schema: dict[str, Any] = json_content.get("schema", {})
    return str(schema.get("description", "")) == "Validation Exception"


def _inject_operation_responses(
    paths: dict[str, Any],
    response_keys: list[str],
    status_for_key: dict[str, str],
) -> None:
    """Inject error response refs into each operation in *paths*.

    Mutates *paths* in place -- the caller is responsible for
    passing a deep-copied schema.
    """
    for path, path_item in paths.items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            op_responses = operation["responses"]
            for key in response_keys:
                status_code = status_for_key[key]
                if not _should_inject(key, path, method, operation):
                    continue
                if status_code == "400":
                    # Only replace Litestar's auto-generated
                    # ValidationException 400; preserve custom 400s.
                    existing = op_responses.get("400")
                    if existing is None or _is_litestar_validation_400(
                        existing,
                    ):
                        op_responses["400"] = _response_ref(key)
                elif status_code not in op_responses:
                    op_responses[status_code] = _response_ref(key)


# ── Extracted steps ───────────────────────────────────────────


def _add_problem_detail_schema(schemas: dict[str, Any]) -> None:
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
    responses: dict[str, Any],
) -> tuple[list[str], dict[str, str]]:
    """Build reusable error responses and return keys + status mapping."""
    response_keys: list[str] = []
    status_for_key: dict[str, str] = {}
    for spec in _ERROR_RESPONSES:
        responses[spec.key] = _build_reusable_response(spec)
        response_keys.append(spec.key)
        status_for_key[spec.key] = str(spec.status)
    return response_keys, status_for_key


def _update_info_description(info: dict[str, Any]) -> None:
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


# ── Main function ─────────────────────────────────────────────


def inject_rfc9457_responses(schema: dict[str, Any]) -> dict[str, Any]:
    """Inject RFC 9457 dual-format error responses into an OpenAPI schema.

    Takes the raw schema dict produced by Litestar's
    ``app.openapi_schema.to_schema()`` and returns a **new** dict with:

    - Nullable ``oneOf`` unions flattened to JSON Schema 2020-12
      ``type`` arrays (fixes API doc renderers validation warnings)
    - ``ProblemDetail`` added to ``components.schemas``
    - Reusable error responses (dual content types) in
      ``components.responses``
    - Error response refs injected into every operation
    - RFC 9457 docs stored in ``info.x-documentation``

    Args:
        schema: OpenAPI schema dict (not modified).

    Returns:
        Enhanced copy of the schema.
    """
    result: dict[str, Any] = copy.deepcopy(schema)

    components = result.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    responses = components.setdefault("responses", {})

    _add_problem_detail_schema(schemas)
    response_keys, status_for_key = _build_all_responses(responses)
    _inject_operation_responses(
        result.get("paths", {}),
        response_keys,
        status_for_key,
    )
    _update_info_description(result.setdefault("info", {}))

    # Normalize after all schemas are in place (including ProblemDetail).
    # Workaround for renderer bug -- see issue #268 for details
    result = _normalize_nullable_unions(result, all_schemas=schemas)

    path_count = len(result.get("paths", {}))
    logger.debug(
        API_OPENAPI_SCHEMA_ENHANCED,
        paths_processed=path_count,
        responses_added=len(response_keys),
    )

    return result
