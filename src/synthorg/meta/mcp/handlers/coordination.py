"""Coordination domain MCP handlers.

Wires 9 tools across coordination, scaling, and ceremony-policy to
their service facades:

- :class:`CoordinationService` (``get_task_metrics``, ``metrics_list``)
- :class:`ScalingDecisionService` (``scaling_list_decisions``,
  ``_get_decision``, ``_get_config``, ``_trigger``)
- :class:`CeremonyPolicyService` (``ceremony_policy_get``,
  ``_get_resolved``, ``_get_active_strategy``)

Handlers gracefully degrade to ``capability_gap`` when the
corresponding service is not attached to ``app_state`` (happens in
stripped-down unit environments); production deployments wire the
services in the application bootstrap.
"""

import copy
from collections.abc import Mapping  # noqa: TC003 -- PEP 649 annotation
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from synthorg.api.errors import NotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    invalid_argument,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    capability_gap,
    dump_many,
    err,
    ok,
)
from synthorg.meta.mcp.handlers.common_args import (
    coerce_pagination,
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

_WHY_COORDINATION_NOT_WIRED = (
    "coordination_service is not attached to app_state; wire it in "
    "application bootstrap"
)
_WHY_SCALING_NOT_WIRED = (
    "scaling_decision_service is not attached to app_state; wire it "
    "in application bootstrap"
)
_WHY_CEREMONY_NOT_WIRED = (
    "ceremony_policy_service is not attached to app_state; wire it "
    "in application bootstrap"
)


# --- Coordination ---------------------------------------------------------


async def _coordination_get_task_metrics(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_coordination_get_task_metrics"
    try:
        task_id = require_non_blank(arguments, "task_id")
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if not getattr(app_state, "has_coordination_service", False):
        return capability_gap(tool, _WHY_COORDINATION_NOT_WIRED)
    try:
        record = await app_state.coordination_service.get_task_metrics(
            NotBlankStr(task_id),
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        missing = NotFoundError(
            f"No coordination metrics recorded for task {task_id!r}",
        )
        log_handler_invoke_failed(tool, missing, task_id=str(task_id))
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=record.model_dump(mode="json"))


async def _coordination_metrics_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_coordination_metrics_list"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if not getattr(app_state, "has_coordination_service", False):
        return capability_gap(tool, _WHY_COORDINATION_NOT_WIRED)
    try:
        records, total = await app_state.coordination_service.list_metrics(
            offset=offset,
            limit=limit,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(records), pagination=meta)


# --- Scaling --------------------------------------------------------------


async def _scaling_list_decisions(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_scaling_list_decisions"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if not getattr(app_state, "has_scaling_decision_service", False):
        return capability_gap(tool, _WHY_SCALING_NOT_WIRED)
    try:
        decisions, total = await app_state.scaling_decision_service.list_decisions(
            offset=offset,
            limit=limit,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(decisions), pagination=meta)


async def _scaling_get_decision(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_scaling_get_decision"
    try:
        decision_id = require_non_blank(arguments, "decision_id")
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if not getattr(app_state, "has_scaling_decision_service", False):
        return capability_gap(tool, _WHY_SCALING_NOT_WIRED)
    try:
        decision = await app_state.scaling_decision_service.get_decision(
            NotBlankStr(decision_id),
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if decision is None:
        missing = NotFoundError(f"Scaling decision {decision_id!r} not found")
        log_handler_invoke_failed(tool, missing, decision_id=str(decision_id))
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=decision.model_dump(mode="json"))


async def _scaling_get_config(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_scaling_get_config"
    if not getattr(app_state, "has_scaling_decision_service", False):
        return capability_gap(tool, _WHY_SCALING_NOT_WIRED)
    try:
        config = await app_state.scaling_decision_service.get_config()
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=config.model_dump(mode="json"))


async def _scaling_trigger(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_scaling_trigger"
    raw_ids = arguments.get("agent_ids")
    if raw_ids is None or not isinstance(raw_ids, (list, tuple)):
        bad = invalid_argument("agent_ids", "list of non-blank strings")
        log_handler_argument_invalid(tool, bad)
        return err(bad)
    try:
        agent_ids = tuple(
            NotBlankStr(_require_non_blank_value(v, "agent_ids")) for v in raw_ids
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if not agent_ids:
        empty = invalid_argument("agent_ids", "non-empty list")
        log_handler_argument_invalid(tool, empty)
        return err(empty)
    if not getattr(app_state, "has_scaling_decision_service", False):
        return capability_gap(tool, _WHY_SCALING_NOT_WIRED)
    try:
        decisions = await app_state.scaling_decision_service.trigger(agent_ids)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(decisions))


# --- Ceremony policy ------------------------------------------------------


async def _ceremony_policy_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_ceremony_policy_get"
    if not getattr(app_state, "has_ceremony_policy_service", False):
        return capability_gap(tool, _WHY_CEREMONY_NOT_WIRED)
    try:
        policy = await app_state.ceremony_policy_service.get_policy()
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=policy.model_dump(mode="json"))


async def _ceremony_policy_get_resolved(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_ceremony_policy_get_resolved"
    if not getattr(app_state, "has_ceremony_policy_service", False):
        return capability_gap(tool, _WHY_CEREMONY_NOT_WIRED)
    department: NotBlankStr | None = None
    if "department" in arguments:
        department_raw = arguments["department"]
        # Reject null AND empty / non-string. ``.get`` used to
        # conflate "key absent" with "key present but null" and that
        # silently mapped a malformed request to the "no filter"
        # path.
        if department_raw is None:
            exc = invalid_argument("department", _TY_NON_BLANK)
            log_handler_argument_invalid(tool, exc)
            return err(exc)
        if not isinstance(department_raw, str) or not department_raw.strip():
            exc = invalid_argument("department", _TY_NON_BLANK)
            log_handler_argument_invalid(tool, exc)
            return err(exc)
        department = NotBlankStr(department_raw.strip())
    try:
        resolved = await app_state.ceremony_policy_service.get_resolved_policy(
            department=department,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=resolved.model_dump(mode="json"))


async def _ceremony_policy_get_active_strategy(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_ceremony_policy_get_active_strategy"
    if not getattr(app_state, "has_ceremony_policy_service", False):
        return capability_gap(tool, _WHY_CEREMONY_NOT_WIRED)
    try:
        active = await app_state.ceremony_policy_service.get_active_strategy()
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=active.model_dump(mode="json"))


def _require_non_blank_value(value: Any, arg_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise invalid_argument(arg_name, _TY_NON_BLANK)
    return value.strip()


COORDINATION_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    copy.deepcopy(
        {
            "synthorg_coordination_get_task_metrics": _coordination_get_task_metrics,
            "synthorg_coordination_metrics_list": _coordination_metrics_list,
            "synthorg_scaling_list_decisions": _scaling_list_decisions,
            "synthorg_scaling_get_decision": _scaling_get_decision,
            "synthorg_scaling_get_config": _scaling_get_config,
            "synthorg_scaling_trigger": _scaling_trigger,
            "synthorg_ceremony_policy_get": _ceremony_policy_get,
            "synthorg_ceremony_policy_get_resolved": _ceremony_policy_get_resolved,
            "synthorg_ceremony_policy_get_active_strategy": (
                _ceremony_policy_get_active_strategy
            ),
        },
    ),
)
