"""Typed-parse helper for the registered API entry-point boundaries.

Six boundaries in SynthOrg accept payloads as ``dict[str, Any]`` even
though typed Pydantic models exist for each role: MCP tool handler
arguments, JWT claim decoding, WebSocket control messages, audit-chain
event payloads, A2A JSON-RPC params, and the settings security export.

This module ships the canonical entry-point validator. Call sites that
formerly extracted fields from a raw dict by string key now route through
:func:`parse_typed`, which validates against the boundary's typed model
and emits a structured log on failure. The intent is to make the
``dict`` -> typed migration land incrementally without forcing every
handler to invent its own ``ValidationError`` translation.

Usage::

    from synthorg.api.boundary import parse_typed
    from synthorg.api.dto import JwtClaims

    claims = parse_typed("jwt", raw_payload, JwtClaims)
    user_id = claims.sub
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from synthorg.observability import get_logger
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

    The boundary name is opaque -- callers pick a stable label so the
    structured-log search can group failures (``mcp.tool``, ``jwt``,
    ``ws.control``, ``audit_chain``, ``a2a.jsonrpc``, ``settings.security``).

    Failures emit one ``API_BOUNDARY_VALIDATION_FAILED`` log carrying
    the boundary name, the exception class, the failure count, and up
    to five field locations, then re-raise the ``ValidationError``. The
    caller is responsible for translating that into the appropriate HTTP
    response or RPC error envelope -- this helper does not swallow.
    """
    if raw is None:
        raw = {}
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        logger.warning(
            API_BOUNDARY_VALIDATION_FAILED,
            boundary=boundary,
            error_type=type(exc).__name__,
            error_count=len(exc.errors()),
            error_locations=tuple(
                ".".join(str(part) for part in error["loc"])
                for error in exc.errors()[:_MAX_LOGGED_LOCATIONS]
            ),
        )
        raise


__all__ = ["parse_typed"]
