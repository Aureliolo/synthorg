"""Signal domain MCP handlers.

9 tools backing the Chief-of-Staff agent's org-health view: composite
org snapshot, six per-domain summaries (performance, budget,
coordination, scaling, errors, evolution), proposal listing, and
proposal submission.

All handlers shim through :class:`SignalsService` exposed on
``AppState``; per-window reads thread ``since`` / ``until`` from the
MCP arguments, and the write path
(``synthorg_signals_submit_proposal``) is destructive and passes
through :func:`require_destructive_guardrails`.
"""

from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from synthorg.core.enums import ApprovalStatus
from synthorg.meta.mcp.errors import invalid_argument
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    ok,
    require_destructive_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import coerce_pagination
from synthorg.meta.models import ImprovementProposal
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_DESTRUCTIVE_OP_EXECUTED,
    MCP_HANDLER_INVOKE_FAILED,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)

_ARG_SINCE = "since"
_ARG_UNTIL = "until"
_ARG_STATUS = "status"
_ARG_PROPOSAL = "proposal"
_TY_ISO_DT = "ISO 8601 datetime string"
_TY_TZ_AWARE = "timezone-aware ISO 8601"
_TY_WINDOW_ORDER = "earlier than until"
_TY_APPROVAL_STATUS = "ApprovalStatus string"
_TY_PROPOSAL_OBJ = "ImprovementProposal object"
_TY_PROPOSAL_SCHEMA = "valid ImprovementProposal schema"


def _parse_window(arguments: dict[str, Any]) -> tuple[datetime, datetime]:
    """Extract and validate ``since`` / ``until`` datetimes from arguments.

    ``since`` is required; ``until`` defaults to now.  Both must be
    ISO 8601 strings with a timezone indicator; naive values are
    rejected as invalid arguments.
    """
    raw_since = arguments.get(_ARG_SINCE)
    if not isinstance(raw_since, str) or not raw_since.strip():
        raise invalid_argument(_ARG_SINCE, _TY_ISO_DT)
    try:
        since = datetime.fromisoformat(raw_since)
    except ValueError as exc:
        raise invalid_argument(_ARG_SINCE, _TY_ISO_DT) from exc
    if since.tzinfo is None:
        raise invalid_argument(_ARG_SINCE, _TY_TZ_AWARE)
    raw_until = arguments.get(_ARG_UNTIL)
    if raw_until is None or raw_until == "":
        until = datetime.now(UTC)
    else:
        if not isinstance(raw_until, str):
            raise invalid_argument(_ARG_UNTIL, _TY_ISO_DT)
        try:
            until = datetime.fromisoformat(raw_until)
        except ValueError as exc:
            raise invalid_argument(_ARG_UNTIL, _TY_ISO_DT) from exc
        if until.tzinfo is None:
            raise invalid_argument(_ARG_UNTIL, _TY_TZ_AWARE)
    if since >= until:
        raise invalid_argument(_ARG_SINCE, _TY_WINDOW_ORDER)
    return since, until


def _parse_pagination(arguments: dict[str, Any]) -> tuple[int, int]:
    """Extract offset/limit with defaults."""
    return coerce_pagination(arguments)


def _parse_status(arguments: dict[str, Any]) -> ApprovalStatus | None:
    """Extract and validate the optional ``status`` filter."""
    status_raw = arguments.get(_ARG_STATUS)
    if status_raw in (None, ""):
        return None
    if not isinstance(status_raw, str):
        raise invalid_argument(_ARG_STATUS, _TY_APPROVAL_STATUS)
    try:
        return ApprovalStatus(status_raw)
    except ValueError as exc:
        raise invalid_argument(_ARG_STATUS, _TY_APPROVAL_STATUS) from exc


def _parse_proposal(arguments: dict[str, Any]) -> ImprovementProposal:
    """Decode the ``proposal`` argument into a validated model."""
    raw_proposal = arguments.get(_ARG_PROPOSAL)
    if not isinstance(raw_proposal, dict):
        raise invalid_argument(_ARG_PROPOSAL, _TY_PROPOSAL_OBJ)
    try:
        return ImprovementProposal.model_validate(raw_proposal)
    except ValidationError as exc:
        raise invalid_argument(_ARG_PROPOSAL, _TY_PROPOSAL_SCHEMA) from exc


def _tool(name: str) -> dict[str, str]:
    """Thin helper for logging context."""
    return {"tool": name}


def _actor_id(actor: AgentIdentity | None) -> str | None:
    """Return a stable audit identifier for ``actor`` (prefers ``.id``)."""
    if actor is None:
        return None
    agent_id = getattr(actor, "id", None)
    if agent_id is not None:
        return str(agent_id)
    name = getattr(actor, "name", None)
    return name if isinstance(name, str) and name else None


async def _snapshot(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    try:
        since, until = _parse_window(arguments)
        snapshot = await app_state.signals_service.get_org_snapshot(
            since=since,
            until=until,
        )
        return ok(snapshot.model_dump(mode="json"))
    except Exception as exc:
        logger.warning(
            MCP_HANDLER_INVOKE_FAILED,
            **_tool("synthorg_signals_get_org_snapshot"),
            error=safe_error_description(exc),
        )
        return err(exc)


def _make_window_handler(
    *,
    tool_name: str,
    method_name: str,
) -> ToolHandler:
    """Build a windowed-read handler dispatching to ``signals_service.<method>``."""

    async def handler(
        *,
        app_state: Any,
        arguments: dict[str, Any],
        actor: AgentIdentity | None = None,  # noqa: ARG001
    ) -> str:
        try:
            since, until = _parse_window(arguments)
            fn: Callable[..., Any] = getattr(app_state.signals_service, method_name)
            result = await fn(since=since, until=until)
            return ok(result.model_dump(mode="json"))
        except Exception as exc:
            logger.warning(
                MCP_HANDLER_INVOKE_FAILED,
                **_tool(tool_name),
                error=safe_error_description(exc),
            )
            return err(exc)

    return handler


async def _list_proposals(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    try:
        offset, limit = _parse_pagination(arguments)
        status = _parse_status(arguments)
        page, total = await app_state.signals_service.list_proposals(
            status=status,
            offset=offset,
            limit=limit,
        )
        pagination_meta = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok(dump_many(page), pagination=pagination_meta)
    except Exception as exc:
        logger.warning(
            MCP_HANDLER_INVOKE_FAILED,
            **_tool("synthorg_signals_get_proposals"),
            error=safe_error_description(exc),
        )
        return err(exc)


async def _submit_proposal(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    tool_name = "synthorg_signals_submit_proposal"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        proposal = _parse_proposal(arguments)
        item = await app_state.signals_service.submit_proposal(
            proposal=proposal,
            actor=resolved_actor,
            reason=reason,
        )
        logger.info(
            MCP_DESTRUCTIVE_OP_EXECUTED,
            tool_name=tool_name,
            actor_agent_id=_actor_id(resolved_actor),
            reason=reason,
            target_id=str(item.id),
        )
        return ok(item.model_dump(mode="json"))
    except Exception as exc:
        logger.warning(
            MCP_HANDLER_INVOKE_FAILED,
            **_tool(tool_name),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return err(exc)


SIGNAL_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_signals_get_org_snapshot": _snapshot,
        "synthorg_signals_get_performance": _make_window_handler(
            tool_name="synthorg_signals_get_performance",
            method_name="get_performance",
        ),
        "synthorg_signals_get_budget": _make_window_handler(
            tool_name="synthorg_signals_get_budget",
            method_name="get_budget",
        ),
        "synthorg_signals_get_coordination": _make_window_handler(
            tool_name="synthorg_signals_get_coordination",
            method_name="get_coordination",
        ),
        "synthorg_signals_get_scaling_history": _make_window_handler(
            tool_name="synthorg_signals_get_scaling_history",
            method_name="get_scaling_history",
        ),
        "synthorg_signals_get_error_patterns": _make_window_handler(
            tool_name="synthorg_signals_get_error_patterns",
            method_name="get_error_patterns",
        ),
        "synthorg_signals_get_evolution_outcomes": _make_window_handler(
            tool_name="synthorg_signals_get_evolution_outcomes",
            method_name="get_evolution_outcomes",
        ),
        "synthorg_signals_get_proposals": _list_proposals,
        "synthorg_signals_submit_proposal": _submit_proposal,
    },
)
