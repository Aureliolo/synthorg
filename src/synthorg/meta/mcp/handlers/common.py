"""Shared response/output helpers for MCP tool handlers.

Every real handler imports from this module.  The file provides three
concerns in one place:

1. **Response envelope** (``ok``, ``err``, ``PaginationMeta``,
   ``not_supported``, ``capability_gap``, ``service_fallback``) -- builds
   the JSON string the handler returns to the invoker.
2. **Output helpers** (``dump_many``, ``paginate_sequence``) --
   Pydantic batch serialisation and in-memory pagination of an
   already-materialised sequence.
3. **Guardrails** (``require_destructive_guardrails``) -- single source
   of truth for the ``confirm=True`` + non-blank ``reason`` + non-None
   ``actor`` triple enforced on every destructive tool.

Argument-validation helpers (``require_arg``, ``require_non_blank``,
``actor_id``, ``coerce_pagination``, plus six newer extractors) live in
:mod:`synthorg.meta.mcp.handlers.common_args`. Structured-logging
helpers for the three handler-side log paths (argument-invalid,
invoke-failed, guardrail-violated) live in
:mod:`synthorg.meta.mcp.handlers.common_logging`.

The placeholder scaffold (``make_placeholder_handler``,
``make_handlers_for_tools``) stays in this module; real handlers never
call it.  The placeholder logs at WARNING via the
``MCP_HANDLER_NOT_IMPLEMENTED`` event so ops can alert on unwired tools.
"""

import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.meta.mcp.errors import guardrail_violation, invalid_argument
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- runtime annotation on placeholder factories
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_HANDLER_CAPABILITY_GAP,
    MCP_HANDLER_NOT_IMPLEMENTED,
    MCP_HANDLER_SERVICE_FALLBACK,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)

_ARG_OFFSET = "offset"
_ARG_LIMIT = "limit"
_TY_NON_NEG_INT = "non-negative int"
_TY_POS_INT = "positive int"

_GR_MISSING_ACTOR: Literal["missing_actor"] = "missing_actor"
_GR_MISSING_CONFIRM: Literal["missing_confirm"] = "missing_confirm"
_GR_MISSING_REASON: Literal["missing_reason"] = "missing_reason"
_GR_MSG_ACTOR = "Destructive operation requires an identified actor"
_GR_MSG_CONFIRM = "Destructive operation requires 'confirm': true"
_GR_MSG_REASON = "Destructive operation requires a non-blank 'reason'"

_DC_NOT_SUPPORTED = "not_supported"


class PaginationMeta(BaseModel):
    """Pagination metadata attached to list/collection responses.

    Attributes:
        total: Unfiltered total count as reported by the service.
            Never synthesised by the handler; always the service's
            own number.
        offset: Starting offset applied to this page.
        limit: Page size applied to this page.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(gt=0)


def ok(
    data: Any = None,
    *,
    pagination: PaginationMeta | None = None,
) -> str:
    """Build an ``ok`` success envelope as a JSON string.

    The ``data`` key is always present (even when ``None``) so callers
    get a stable wire shape regardless of whether the handler has a
    payload -- e.g. destructive operations that return ``ok()`` after
    a successful mutation still emit ``{"status": "ok", "data": null}``.

    Args:
        data: JSON-serialisable payload (dict / list / scalar / ``None``).
            Pydantic models must be pre-dumped by the caller via
            ``.model_dump(mode="json")`` or the ``dump_many`` helper.
        pagination: Optional pagination metadata; present only on
            list/collection responses.

    Returns:
        JSON-encoded envelope suitable for direct return from an MCP
        tool handler.
    """
    body: dict[str, Any] = {"status": "ok", "data": data}
    if pagination is not None:
        body["pagination"] = pagination.model_dump(mode="json")
    return json.dumps(body)


def err(
    exc: Exception,
    *,
    domain_code: str | None = None,
) -> str:
    """Build a domain-error envelope as a JSON string.

    ``message`` goes through ``safe_error_description`` so tracebacks
    and frame-local state never leak into the envelope (SEC-1).

    Args:
        exc: The caught exception.
        domain_code: Optional explicit code; when omitted falls back to
            ``exc.domain_code`` (set by ``ArgumentValidationError`` /
            ``GuardrailViolationError``) and is otherwise absent.

    Returns:
        JSON-encoded envelope with ``status="error"``.
    """
    body: dict[str, Any] = {
        "status": "error",
        "error_type": type(exc).__name__,
        "message": safe_error_description(exc),
    }
    resolved_code = (
        domain_code
        if domain_code is not None
        else getattr(
            exc,
            "domain_code",
            None,
        )
    )
    if resolved_code is not None:
        body["domain_code"] = resolved_code
    return json.dumps(body)


def _actor_has_identifier(actor: Any) -> bool:
    """Return ``True`` when ``actor`` carries an audit-usable identifier.

    The destructive-op audit trail is meaningless without a stable
    identifier, so we accept either a non-``None`` ``.id`` attribute
    (typically a ``UUID``) or a non-blank ``.name`` string.  A bare
    object that lacks both is treated as "unattributable" and rejected
    alongside ``actor is None``.
    """
    if getattr(actor, "id", None) is not None:
        return True
    name = getattr(actor, "name", None)
    return isinstance(name, str) and bool(name.strip())


def require_destructive_guardrails(
    arguments: dict[str, Any],
    actor: Any,
) -> tuple[str, Any]:
    """Enforce the destructive-op precondition triple.

    A tool is destructive if it removes, cancels, rejects, rolls back,
    or uninstalls state (see plan for the canonical list).  Every such
    tool's handler calls this helper first.

    Preconditions, in order:
    1. ``actor`` is not ``None`` *and* carries an audit-usable
       identifier (``.id`` or a non-blank ``.name``) so the
       ``MCP_DESTRUCTIVE_OP_EXECUTED`` event has real attribution;
    2. ``arguments["confirm"]`` is the Python literal ``True`` (not
       truthy-but-non-bool);
    3. ``arguments["reason"]`` is a non-blank string.

    Args:
        arguments: Parsed tool arguments.
        actor: The calling agent identity, or ``None`` when the invoker
            was not supplied one.

    Returns:
        Tuple of (reason, actor) -- both already validated.

    Raises:
        GuardrailViolationError: On any precondition failure.  The
            ``violation`` attribute distinguishes the failure mode.
    """
    if actor is None or not _actor_has_identifier(actor):
        raise guardrail_violation(_GR_MISSING_ACTOR, _GR_MSG_ACTOR)
    confirm = arguments.get("confirm")
    if not isinstance(confirm, bool) or confirm is not True:
        raise guardrail_violation(_GR_MISSING_CONFIRM, _GR_MSG_CONFIRM)
    reason = arguments.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise guardrail_violation(_GR_MISSING_REASON, _GR_MSG_REASON)
    return reason, actor


def dump_many(models: Iterable[BaseModel]) -> list[dict[str, Any]]:
    """Serialise a batch of Pydantic models to JSON-mode dicts.

    Args:
        models: Any iterable (tuple, list, generator) of Pydantic models.

    Returns:
        List of dicts, one per input model, suitable for inclusion in
        an ``ok(data=...)`` envelope.
    """
    return [m.model_dump(mode="json") for m in models]


def paginate_sequence[T](
    seq: Sequence[T],
    *,
    offset: int,
    limit: int,
    total: int | None = None,
) -> tuple[list[T], PaginationMeta]:
    """Slice an already-materialised sequence into a page + metadata.

    Used when the underlying service returns the full collection in one
    call (typical of aggregator-style domains: signals, parts of
    analytics).  Services that natively accept offset/limit should
    translate arguments and return ``(items, total)`` directly rather
    than calling this helper.

    Args:
        seq: Full collection from the service.
        offset: Page offset.
        limit: Page size.
        total: Unfiltered total count as reported by the service.  When
            omitted, falls back to ``len(seq)`` (valid iff ``seq`` is
            already the full set).

    Returns:
        Tuple of (page slice, pagination metadata).

    Raises:
        ArgumentValidationError: If ``offset`` is negative or ``limit``
            is non-positive.
    """
    if offset < 0:
        raise invalid_argument(_ARG_OFFSET, _TY_NON_NEG_INT)
    if limit <= 0:
        raise invalid_argument(_ARG_LIMIT, _TY_POS_INT)
    resolved_total = total if total is not None else len(seq)
    page = list(seq[offset : offset + limit])
    return page, PaginationMeta(total=resolved_total, offset=offset, limit=limit)


def _not_supported_envelope(reason: str) -> str:
    """Build the shared ``not_supported`` JSON envelope string."""
    body: dict[str, Any] = {
        "status": "error",
        "error_type": "NotSupportedInMCP",
        "message": reason,
        "domain_code": _DC_NOT_SUPPORTED,
    }
    return json.dumps(body)


def not_supported(tool_name: str, reason: str) -> str:
    """Build a ``not_supported`` envelope for unwired placeholder tools.

    Used by :func:`make_placeholder_handler` for tools that are
    registered but have no concrete handler implementation yet.  Emits
    :data:`MCP_HANDLER_NOT_IMPLEMENTED` so ops alerting can distinguish
    pure placeholders from concrete handlers that happen to lack a
    service facade (the latter use :func:`service_fallback`).

    Args:
        tool_name: Full ``synthorg_<domain>_<action>`` name.
        reason: Short operator-readable reason (which method / backlog
            link).

    Returns:
        JSON-encoded error envelope with
        ``status="error"``, ``domain_code="not_supported"``.
    """
    logger.warning(
        MCP_HANDLER_NOT_IMPLEMENTED,
        tool_name=tool_name,
        reason=reason,
    )
    return _not_supported_envelope(reason)


def service_fallback(tool_name: str, reason: str) -> str:
    """Build a ``not_supported`` envelope for concrete service-fallback handlers.

    Used by real handler implementations whose underlying service layer
    does not yet expose a matching method.  Emits
    :data:`MCP_HANDLER_SERVICE_FALLBACK` so ops telemetry can
    distinguish these "live handler, missing facade" events from pure
    placeholder handlers (which emit
    :data:`MCP_HANDLER_NOT_IMPLEMENTED` via :func:`not_supported`).
    The response envelope is byte-for-byte identical to
    :func:`not_supported`; only the emitted event differs.

    Args:
        tool_name: Full ``synthorg_<domain>_<action>`` name.
        reason: Short operator-readable reason (which method / backlog
            link).

    Returns:
        JSON-encoded error envelope with
        ``status="error"``, ``domain_code="not_supported"``.
    """
    logger.warning(
        MCP_HANDLER_SERVICE_FALLBACK,
        tool_name=tool_name,
        reason=reason,
    )
    return _not_supported_envelope(reason)


def capability_gap(tool_name: str, reason: str) -> str:
    """Build a ``not_supported`` envelope for a wired handler with a primitive gap.

    Identical wire shape to :func:`service_fallback`, but emits the
    dedicated :data:`MCP_HANDLER_CAPABILITY_GAP` event so ops telemetry
    can distinguish "handler wired, primitive does not yet expose the
    required method" from "handler unwired"
    (:func:`make_placeholder_handler`) and from "live handler, but the
    service facade is still a placeholder" (:func:`service_fallback`).

    ``MCP_HANDLER_SERVICE_FALLBACK`` is reserved for the legacy
    ``service_fallback`` helper; META-MCP-2 acceptance asserts zero
    emissions of that event at runtime.

    Args:
        tool_name: Full ``synthorg_<domain>_<action>`` name.
        reason: Short operator-readable reason.

    Returns:
        JSON-encoded error envelope with ``status="error"``,
        ``domain_code="not_supported"``.
    """
    logger.info(
        MCP_HANDLER_CAPABILITY_GAP,
        tool_name=tool_name,
        reason=reason,
    )
    return _not_supported_envelope(reason)


def make_placeholder_handler(tool_name: str) -> ToolHandler:
    """Build a placeholder that returns the standard ``not_supported`` envelope.

    Used for tools registered after PR1 that haven't been given a real
    handler yet.  The returned callable delegates to
    :func:`not_supported` so unwired tools ship the single agreed
    envelope format (``status="error"``, ``domain_code="not_supported"``)
    instead of the legacy ``not_implemented`` string.  ``not_supported``
    also emits the ``MCP_HANDLER_NOT_IMPLEMENTED`` WARNING event, so ops
    alerting continues to see unwired tools exactly as before.

    Args:
        tool_name: Tool name for the envelope + log payload.

    Returns:
        ``ToolHandler`` conforming async handler function.
    """
    reason = (
        f"Tool {tool_name!r} is registered but its service layer "
        "handler is not yet implemented."
    )

    async def handler(
        *,
        app_state: Any,  # noqa: ARG001
        arguments: dict[str, Any],  # noqa: ARG001
        actor: AgentIdentity | None = None,  # noqa: ARG001
    ) -> str:
        return not_supported(tool_name, reason)

    return handler


def make_handlers_for_tools(
    tool_names: tuple[str, ...],
) -> dict[str, ToolHandler]:
    """Create placeholder handlers for a set of tool names.

    Args:
        tool_names: Tuple of tool name strings.

    Returns:
        Dict mapping tool names to placeholder handlers.
    """
    return {name: make_placeholder_handler(name) for name in tool_names}
