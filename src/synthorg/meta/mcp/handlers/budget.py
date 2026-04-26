"""Budget domain MCP handlers.

Shims the 5 budget tools onto ``app_state.cost_tracker`` and
``app_state.config_resolver``.  Version-history reads route through a
:class:`~synthorg.budget.version_service.BudgetConfigVersionsService`
facade obtained via :func:`_versions_service` (which prefers
``app_state.budget_versions_service`` when bootstrap has wired one and
falls back to per-call construction for compatibility with legacy
app_states).  All budget tools are reads; none are destructive.
"""

import copy
from collections.abc import Mapping  # noqa: TC003 -- PEP 649 annotation
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from synthorg.budget.version_service import BudgetConfigVersionsService
from synthorg.meta.mcp.errors import ArgumentValidationError, invalid_argument
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    dump_many,
    err,
    ok,
    paginate_sequence,
)
from synthorg.meta.mcp.handlers.common_args import (
    coerce_pagination,
    require_arg,
    require_non_blank,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)


_TY_NON_BLANK = "non-blank string"
_TY_POS_INT = "positive int"
_ARG_AGENT_ID = "agent_id"
_ARG_TASK_ID = "task_id"
_ARG_VERSION = "version_num"


def _versions_service(app_state: Any) -> BudgetConfigVersionsService:
    """Return the budget-versions service facade.

    Prefers ``app_state.budget_versions_service`` when bootstrap has
    wired one (keeps the handler off ``persistence.*``).  Falls back to
    per-call construction from the persistence primitive for app_states
    that have not adopted the cached-service pattern yet, mirroring the
    :func:`synthorg.meta.mcp.handlers.memory._service` pattern.
    """
    cached: BudgetConfigVersionsService | None = getattr(
        app_state,
        "budget_versions_service",
        None,
    )
    if cached is not None:
        return cached
    return BudgetConfigVersionsService(
        version_repo=app_state.persistence.budget_config_versions,
    )


class _NotFoundError(LookupError):
    """Handler-local not-found signal (budget config version missing)."""

    domain_code = "not_found"


async def _budget_get_config(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_budget_get_config"
    try:
        config = await app_state.config_resolver.get_budget_config()
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=config.model_dump(mode="json"))


async def _budget_list_records(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_budget_list_records"
    try:
        agent_id = arguments.get("agent_id")
        task_id = arguments.get("task_id")
        if agent_id is not None and (
            not isinstance(agent_id, str) or not agent_id.strip()
        ):
            raise invalid_argument(_ARG_AGENT_ID, _TY_NON_BLANK)
        if task_id is not None and (
            not isinstance(task_id, str) or not task_id.strip()
        ):
            raise invalid_argument(_ARG_TASK_ID, _TY_NON_BLANK)
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    try:
        records = await app_state.cost_tracker.get_records(
            agent_id=agent_id,
            task_id=task_id,
        )
        page, meta = paginate_sequence(records, offset=offset, limit=limit)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(page), pagination=meta)


async def _budget_get_agent_spending(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_budget_get_agent_spending"
    try:
        agent_id = require_non_blank(arguments, _ARG_AGENT_ID)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    try:
        total = await app_state.cost_tracker.get_agent_cost(agent_id)
        config = await app_state.config_resolver.get_budget_config()
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(
        data={
            "agent_id": agent_id,
            "total_cost": total,
            "currency": config.currency,
        },
    )


async def _budget_versions_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_budget_versions_list"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    try:
        versions, total = await _versions_service(app_state).list_versions(
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    # The service already returned the requested page; build the
    # envelope meta directly with the repo's true total count.
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(versions), pagination=meta)


async def _budget_versions_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_budget_versions_get"
    try:
        version_num = require_arg(arguments, _ARG_VERSION, int)
        if version_num < 1:
            raise invalid_argument(_ARG_VERSION, _TY_POS_INT)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    try:
        snapshot = await _versions_service(app_state).get_version(version_num)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if snapshot is None:
        missing = _NotFoundError(
            f"Budget config version {version_num} not found",
        )
        log_handler_invoke_failed(tool, missing)
        return err(missing)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=snapshot.model_dump(mode="json"))


BUDGET_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    copy.deepcopy(
        {
            "synthorg_budget_get_config": _budget_get_config,
            "synthorg_budget_list_records": _budget_list_records,
            "synthorg_budget_get_agent_spending": _budget_get_agent_spending,
            "synthorg_budget_versions_list": _budget_versions_list,
            "synthorg_budget_versions_get": _budget_versions_get,
        },
    ),
)
