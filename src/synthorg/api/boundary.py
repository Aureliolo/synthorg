"""Typed-parse helper for the registered API entry-point boundaries.

A "boundary" is a typed entry-point where dict payloads from external
sources (MCP tool invocations, JWT decode, WebSocket frames, A2A RPC,
audit-chain emissions, settings export) must be validated against their
corresponding Pydantic models before use. Today these six surfaces
accept ``dict[str, Any]`` even though typed Pydantic models exist for
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

from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_BOUNDARY_VALIDATION_FAILED

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = get_logger(__name__)

_MAX_LOGGED_LOCATIONS = 5


def parse_typed[T: BaseModel](
    boundary: str,
    raw: Mapping[str, object] | None,
    model: type[T],
) -> T:
    """Validate ``raw`` at ``boundary`` against ``model`` and return the typed instance.

    ``boundary`` MUST be a hardcoded literal at the call site, never a
    user-controlled or externally-derived string. Operators search the
    structured logs by this label so a typo or operator-influenced value
    silently misroutes diagnostics. Today the six registered labels are
    ``mcp.tool``, ``jwt``, ``ws.control``, ``audit_chain``,
    ``a2a.jsonrpc``, and ``settings.security``; new boundaries pick a
    stable namespaced constant.

    ``raw`` of ``None`` is treated as an empty dict before validation.
    This lets callers normalise optional / nullable payloads (e.g. a
    JSON-RPC ``params`` field that may be omitted) without branching;
    Pydantic still raises loudly for required fields.

    Failures emit one ``API_BOUNDARY_VALIDATION_FAILED`` log carrying
    the boundary name, the exception class, the failure count, the
    safe-redacted error description, the first
    ``_MAX_LOGGED_LOCATIONS`` field locations, and a ``truncated`` flag
    indicating whether further locations exist beyond the cap, then
    re-raise the ``ValidationError``. The caller is responsible for
    translating that into the appropriate HTTP response or RPC error
    envelope -- this helper does not swallow.
    """
    input_data = raw if raw is not None else {}
    try:
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
