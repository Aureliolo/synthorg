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
from synthorg.meta.mcp.handlers.common_args import (
    actor_id,
    coerce_pagination,
    parse_time_window,
)
from synthorg.meta.mcp.handlers.common_logging import log_handler_invoke_failed
from synthorg.meta.models import ImprovementProposal
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_DESTRUCTIVE_OP_EXECUTED

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)

_ARG_STATUS = "status"
_ARG_PROPOSAL = "proposal"
_TY_APPROVAL_STATUS = "ApprovalStatus string"
_TY_PROPOSAL_OBJ = "ImprovementProposal object"
_TY_PROPOSAL_SCHEMA = "valid ImprovementProposal schema"


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


async def _snapshot(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    try:
        since, until = parse_time_window(arguments, until_required=False)
        snapshot = await app_state.signals_service.get_org_snapshot(
            since=since,
            until=until,
        )
        return ok(snapshot.model_dump(mode="json"))
    except Exception as exc:
        log_handler_invoke_failed("synthorg_signals_get_org_snapshot", exc)
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
            since, until = parse_time_window(arguments, until_required=False)
            fn: Callable[..., Any] = getattr(app_state.signals_service, method_name)
            result = await fn(since=since, until=until)
            return ok(result.model_dump(mode="json"))
        except Exception as exc:
            log_handler_invoke_failed(tool_name, exc)
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
        log_handler_invoke_failed("synthorg_signals_get_proposals", exc)
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
            actor_agent_id=actor_id(resolved_actor),
            reason=reason,
            target_id=str(item.id),
        )
        return ok(item.model_dump(mode="json"))
    except Exception as exc:
        log_handler_invoke_failed(tool_name, exc)
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
