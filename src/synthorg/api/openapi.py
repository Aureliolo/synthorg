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

The reusable error responses and per-operation injection policy live in
``openapi_responses``; the nullable-union normalization lives in
``openapi_normalize``. Called by ``scripts/export_openapi.py`` after
schema generation.

.. note::

    The ``ProblemDetail`` schema rewrites ``$ref`` paths from Pydantic's
    internal ``#/$defs/`` to ``#/components/schemas/``.  This assumes
    the referenced schemas (``ErrorCode``, ``ErrorCategory``) already
    exist in the Litestar-generated ``components.schemas``.
"""

import copy

from pydantic import JsonValue

from synthorg.api.openapi_normalize import _normalize_nullable_unions
from synthorg.api.openapi_responses import (
    _add_problem_detail_schema,
    _build_all_responses,
    _inject_operation_responses,
    _update_info_description,
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_OPENAPI_SCHEMA_ENHANCED

logger = get_logger(__name__)


def _dict_child(parent: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    """Return ``parent[key]`` as a mutable dict, creating one if absent.

    Mirrors ``dict.setdefault(key, {})`` but narrows the JSON value to a
    concrete object type. A non-object value at *key* is replaced with a
    fresh empty dict.

    Returns:
        The child object stored at *key*.
    """
    child = parent.get(key)
    if not isinstance(child, dict):
        child = {}
        parent[key] = child
    return child


def inject_rfc9457_responses(schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
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
    result: dict[str, JsonValue] = copy.deepcopy(schema)

    components = _dict_child(result, "components")
    schemas = _dict_child(components, "schemas")
    responses = _dict_child(components, "responses")

    _add_problem_detail_schema(schemas)
    response_keys, status_for_key = _build_all_responses(responses)
    paths = result.get("paths")
    if isinstance(paths, dict):
        _inject_operation_responses(paths, response_keys, status_for_key)
    _update_info_description(_dict_child(result, "info"))

    # Normalize after all schemas are in place (including ProblemDetail).
    # Litestar emits ``oneOf: [{type: "string"}, {type: "null"}]`` for
    # nullable primitive fields; ``_normalize_nullable_unions`` flattens
    # those to the idiomatic ``type: ["string", "null"]`` shape that
    # OpenAPI renderers (Scalar, Swagger UI, Redoc) prefer, and converts
    # ``$ref``-based nullable unions to ``anyOf`` so renderers can deref
    # cleanly.
    normalized = _normalize_nullable_unions(result, all_schemas=schemas)
    if isinstance(normalized, dict):
        result = normalized

    final_paths = result.get("paths")
    path_count = len(final_paths) if isinstance(final_paths, dict) else 0
    logger.debug(
        API_OPENAPI_SCHEMA_ENHANCED,
        paths_processed=path_count,
        responses_added=len(response_keys),
    )

    return result
