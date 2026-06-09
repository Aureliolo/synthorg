"""Typed-parse helper for the registered API entry-point boundaries.

A "boundary" is a typed entry-point where dict payloads from external
sources (MCP tool invocations, JWT decode, WebSocket frames, A2A RPC,
audit-chain emissions, settings export) must be validated against their
corresponding Pydantic models before use. Today these six surfaces
accept ``dict[str, JsonValue]`` even though typed Pydantic models exist for
each role.

This module ships the canonical entry-point validator. Call sites that
formerly extracted fields from a raw dict by string key route through
:func:`parse_typed`, which validates against the boundary's typed model
and emits a structured log on failure. The intent is to make the
``dict`` -> typed migration land incrementally without forcing every
handler to invent its own ``ValidationError`` translation.

The helper deliberately stays neutral on error translation: it logs and
re-raises Pydantic's ``ValidationError``, leaving each boundary to
convert that into its native error envelope (HTTP 422 via Litestar at
the REST surface, MCP envelope ``err()`` at the MCP surface, WebSocket
close code 4xxx at the WS surface, A2A JSON-RPC error at the A2A
surface, and so on). Translating in the helper would force a single
shape on six heterogeneous boundaries.

Usage::

    from synthorg.api.boundary import parse_typed
    from synthorg.api.dto import JwtClaims

    claims = parse_typed("jwt", raw_payload, JwtClaims)
    user_id = claims.sub
"""

from collections.abc import (
    Mapping,
)
from typing import Final, LiteralString, overload

from pydantic import BaseModel, TypeAdapter, ValidationError

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_BOUNDARY_VALIDATION_FAILED

logger = get_logger(__name__)

_MAX_LOGGED_LOCATIONS: Final[int] = 5


@overload
def parse_typed[T: BaseModel](
    boundary: LiteralString,
    raw: Mapping[str, object] | None,
    model: type[T],
) -> T: ...


@overload
def parse_typed[T](
    boundary: LiteralString,
    raw: Mapping[str, object] | None,
    model: TypeAdapter[T],
) -> T: ...


def parse_typed[T](
    boundary: LiteralString,
    raw: Mapping[str, object] | None,
    model: type[BaseModel] | TypeAdapter[T],
) -> BaseModel | T:
    """Validate a raw boundary payload against a typed Pydantic model.

    The helper is the canonical entry-point validator for the six
    registered API boundaries (``mcp.tool``, ``jwt``, ``ws.control``,
    ``audit_chain``, ``a2a.jsonrpc``, ``settings.security``). It logs
    the structured failure event on rejection and re-raises the
    underlying ``ValidationError`` so each boundary translates into its
    native error envelope.

    ``boundary`` is typed ``LiteralString`` so the static checker
    rejects any caller passing a runtime-derived string -- the
    operator-search log label cannot be operator-influenced.

    Two validation backends are accepted: a Pydantic model class
    (``type[T]``) for the single-shape boundaries, and a
    :class:`pydantic.TypeAdapter` for boundaries whose contract is a
    discriminated union (e.g. the A2A JSON-RPC params union, where the
    method literal selects the variant).

    Args:
        boundary: Hardcoded namespaced label used for operator log
            search and grouping (e.g. ``"jwt"`` or ``"ws.control"``).
            MUST be a string literal known at type-check time; passing
            a runtime-derived string fails the type check.
        raw: Incoming payload. ``None`` is normalised to ``{}`` so
            callers do not branch on optional / nullable wire fields;
            Pydantic still raises loudly for required fields.
        model: Pydantic model class or ``TypeAdapter`` used for
            validation and coercion.

    Returns:
        The validated typed instance.

    Raises:
        ValidationError: If the payload does not conform to ``model``.
            Before re-raising, emits one ``API_BOUNDARY_VALIDATION_FAILED``
            log carrying the boundary name, exception class, failure
            count, redacted error description, the first
            ``_MAX_LOGGED_LOCATIONS`` field locations, and a
            ``truncated`` flag indicating whether further locations
            exist beyond the cap. The caller is responsible for
            translating the exception into the appropriate HTTP / RPC
            / envelope response -- this helper does not swallow.
    """
    input_data = raw if raw is not None else {}
    try:
        if isinstance(model, TypeAdapter):
            return model.validate_python(input_data)
        return model.model_validate(input_data)
    except ValidationError as exc:
        all_errors = exc.errors()
        truncated = len(all_errors) > _MAX_LOGGED_LOCATIONS
        logger.warning(
            API_BOUNDARY_VALIDATION_FAILED,
            boundary=boundary,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            error_count=len(all_errors),
            error_locations=tuple(
                ".".join(str(part) for part in error["loc"])
                for error in all_errors[:_MAX_LOGGED_LOCATIONS]
            ),
            truncated=truncated,
        )
        raise


__all__ = ["parse_typed"]
