"""Signal domain MCP handlers.

9 tools backing the Chief-of-Staff agent's org-health view: composite
org snapshot, six per-domain summaries (performance, budget,
coordination, scaling, errors, evolution), proposal listing, and
proposal submission.

All handlers shim through :class:`SignalsService` exposed on
``AppState``; per-window reads thread ``since`` / ``until`` from the
MCP arguments, and the write path
(``synthorg_signals_submit_proposal``) is destructive and passes
through :func:`require_admin_guardrails`.
"""

from collections.abc import (
    Callable,  # noqa: TC003 -- PEP 649 annotation
    Mapping,  # noqa: TC003 -- PEP 649 annotation
)
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from synthorg.core.agent import (
    AgentIdentity,  # noqa: TC001 -- typeguard runtime resolution
)
from synthorg.core.enums import ApprovalStatus
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    actor_id,
    coerce_pagination,
    parse_time_window,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.meta.models import ImprovementProposal
from synthorg.meta.state import signals_service_of
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED

logger = get_logger(__name__)

_ARG_STATUS = "status"
_ARG_PROPOSAL = "proposal"
_TY_APPROVAL_STATUS = "ApprovalStatus string"
_TY_PROPOSAL_OBJ = "ImprovementProposal object"
_TY_PROPOSAL_SCHEMA = "valid ImprovementProposal schema"


def _parse_status(arguments: dict[str, Any]) -> ApprovalStatus | None:
    """Extract and validate the optional ``status`` filter.

    Returns:
        The ``ApprovalStatus`` value when present, ``None`` otherwise.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    status_raw = arguments.get(_ARG_STATUS)
    if status_raw in (None, ""):
        return None
    if not isinstance(status_raw, str):
        raise ArgumentValidationError(_ARG_STATUS, _TY_APPROVAL_STATUS)
    try:
        return ApprovalStatus(status_raw)
    except ValueError as exc:
        raise ArgumentValidationError(_ARG_STATUS, _TY_APPROVAL_STATUS) from exc


def _parse_proposal(arguments: dict[str, Any]) -> ImprovementProposal:
    """Decode the ``proposal`` argument into a validated model.

    Returns:
        ``ImprovementProposal`` instance.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw_proposal = arguments.get(_ARG_PROPOSAL)
    if not isinstance(raw_proposal, dict):
        raise ArgumentValidationError(_ARG_PROPOSAL, _TY_PROPOSAL_OBJ)
    try:
        return ImprovementProposal.model_validate(raw_proposal)
    except ValidationError as exc:
        raise ArgumentValidationError(_ARG_PROPOSAL, _TY_PROPOSAL_SCHEMA) from exc


async def _snapshot(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return snapshot."""
    try:
        since, until = parse_time_window(arguments, until_required=False)
        snapshot = await signals_service_of(app_state).get_org_snapshot(
            since=since,
            until=until,
        )
        return ok(snapshot.model_dump(mode="json"))
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_signals_get_org_snapshot", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_signals_get_org_snapshot", exc)
        return err(exc)


def _make_window_handler(
    *,
    tool_name: str,
    method_name: str,
) -> ToolHandler:
    """Build a windowed-read handler dispatching to ``signals_service.<method>``.

    Returns:
        ``ToolHandler`` instance.
    """

    async def handler(
        *,
        app_state: Any,
        arguments: dict[str, Any],
        actor: AgentIdentity | None = None,  # noqa: ARG001
    ) -> str:
        """Return handler."""
        try:
            since, until = parse_time_window(arguments, until_required=False)
            fn: Callable[..., Any] = getattr(signals_service_of(app_state), method_name)
            result = await fn(since=since, until=until)
            return ok(result.model_dump(mode="json"))
        except ArgumentValidationError as exc:
            log_handler_argument_invalid(tool_name, exc)
            return err(exc)
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
    """Return list proposals."""
    try:
        offset, limit = coerce_pagination(arguments)
        status = _parse_status(arguments)
        page, total = await signals_service_of(app_state).list_proposals(
            status=status,
            offset=offset,
            limit=limit,
        )
        pagination_meta = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok(dump_many(page), pagination=pagination_meta)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid("synthorg_signals_get_proposals", exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed("synthorg_signals_get_proposals", exc)
        return err(exc)


async def _submit_proposal(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Return submit proposal."""
    tool_name = "synthorg_signals_submit_proposal"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        proposal = _parse_proposal(arguments)
        item = await signals_service_of(app_state).submit_proposal(
            proposal=proposal,
            actor=resolved_actor,
            reason=reason,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool_name,
            actor_agent_id=actor_id(resolved_actor),
            reason=reason,
            target_id=str(item.id),
        )
        return ok(item.model_dump(mode="json"))
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool_name, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool_name, exc)
        return err(exc)
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
