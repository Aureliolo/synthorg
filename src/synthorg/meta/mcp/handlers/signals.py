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
    Mapping,
)
from types import MappingProxyType
from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.core.agent import (
    AgentIdentity,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.mcp.domains._simple_args import (
    SignalsGetBudgetArgs,
    SignalsGetCoordinationArgs,
    SignalsGetErrorPatternsArgs,
    SignalsGetEvolutionOutcomesArgs,
    SignalsGetOrgSnapshotArgs,
    SignalsGetPerformanceArgs,
    SignalsGetProposalsArgs,
    SignalsGetScalingHistoryArgs,
    SignalsSubmitProposalArgs,
    _SinceOptionalUntilArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    actor_id,
    resolve_time_window,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.meta.models import ImprovementProposal
from synthorg.meta.state import signals_service_of
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_INVOKE_SUCCESS,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARG_PROPOSAL = "proposal"
_TY_PROPOSAL_SCHEMA = "valid ImprovementProposal schema"


async def _snapshot(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_signals_get_org_snapshot`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_signals_get_org_snapshot"
    try:
        args = typed_args(arguments, SignalsGetOrgSnapshotArgs)
        since, until = resolve_time_window(
            args.since,
            args.until,
            until_required=False,
        )
        snapshot = await signals_service_of(app_state).get_org_snapshot(
            since=since,
            until=until,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    response = ok(snapshot.model_dump(mode="json"))
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return response


def _make_window_handler(
    *,
    tool_name: str,
    method_name: str,
    args_model: type[_SinceOptionalUntilArgs],
) -> ToolHandler:
    """Build a windowed-read handler dispatching to ``signals_service.<method>``.

    Returns:
        ``ToolHandler`` instance.
    """

    async def handler(
        *,
        app_state: AppState,
        arguments: dict[str, object],
        actor: AgentIdentity | None = None,  # noqa: ARG001
    ) -> str:
        """Dispatch a windowed signals read to the bound service method.

        Returns:
            JSON-encoded MCP envelope string.
        """
        try:
            args = typed_args(arguments, args_model)
            since, until = resolve_time_window(
                args.since,
                args.until,
                until_required=False,
            )
            fn = getattr(signals_service_of(app_state), method_name)
            result = await fn(since=since, until=until)
        except ArgumentValidationError as exc:
            log_handler_argument_invalid(tool_name, exc)
            return err(exc)
        except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
            reraise_critical(exc)
            log_handler_invoke_failed(tool_name, exc)
            return err(exc)
        response = ok(result.model_dump(mode="json"))
        logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool_name)
        return response

    return handler


async def _list_proposals(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_signals_get_proposals`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_signals_get_proposals"
    try:
        args = typed_args(arguments, SignalsGetProposalsArgs)
        offset, limit = args.offset, args.limit
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        page, total = await signals_service_of(app_state).list_proposals(
            status=args.status,
            offset=offset,
            limit=limit,
        )
        pagination_meta = PaginationMeta(total=total, offset=offset, limit=limit)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    response = ok(dump_many(page), pagination=pagination_meta)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return response


async def _submit_proposal(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_signals_submit_proposal`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.

    Raises:
        ArgumentValidationError: When ``proposal`` is not a valid
            ImprovementProposal payload.
    """
    tool_name = "synthorg_signals_submit_proposal"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        raw_proposal = typed_args(arguments, SignalsSubmitProposalArgs).proposal
        try:
            proposal = ImprovementProposal.model_validate(raw_proposal)
        except ValidationError as exc:
            raise ArgumentValidationError(_ARG_PROPOSAL, _TY_PROPOSAL_SCHEMA) from exc
        item = await signals_service_of(app_state).submit_proposal(
            proposal=proposal,
            actor=resolved_actor,
            reason=reason,
        )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool_name, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool_name, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool_name, exc)
        return err(exc)
    response = ok(item.model_dump(mode="json"))
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool_name)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool_name,
        actor_agent_id=actor_id(resolved_actor),
        reason=reason,
        target_id=str(item.id),
    )
    return response


SIGNAL_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_signals_get_org_snapshot": _snapshot,
        "synthorg_signals_get_performance": _make_window_handler(
            tool_name="synthorg_signals_get_performance",
            method_name="get_performance",
            args_model=SignalsGetPerformanceArgs,
        ),
        "synthorg_signals_get_budget": _make_window_handler(
            tool_name="synthorg_signals_get_budget",
            method_name="get_budget",
            args_model=SignalsGetBudgetArgs,
        ),
        "synthorg_signals_get_coordination": _make_window_handler(
            tool_name="synthorg_signals_get_coordination",
            method_name="get_coordination",
            args_model=SignalsGetCoordinationArgs,
        ),
        "synthorg_signals_get_scaling_history": _make_window_handler(
            tool_name="synthorg_signals_get_scaling_history",
            method_name="get_scaling_history",
            args_model=SignalsGetScalingHistoryArgs,
        ),
        "synthorg_signals_get_error_patterns": _make_window_handler(
            tool_name="synthorg_signals_get_error_patterns",
            method_name="get_error_patterns",
            args_model=SignalsGetErrorPatternsArgs,
        ),
        "synthorg_signals_get_evolution_outcomes": _make_window_handler(
            tool_name="synthorg_signals_get_evolution_outcomes",
            method_name="get_evolution_outcomes",
            args_model=SignalsGetEvolutionOutcomesArgs,
        ),
        "synthorg_signals_get_proposals": _list_proposals,
        "synthorg_signals_submit_proposal": _submit_proposal,
    },
)
