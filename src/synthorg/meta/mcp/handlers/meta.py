"""Meta (self-improvement) domain MCP handlers.

5 tools, all live as of META-MCP-3:

- ``list_mcp_tools`` reflects the tool registry.
- ``get_mcp_server_config`` returns the MCP server metadata.
- ``list_rules`` shims through :class:`CustomRulesService`.
- ``get_config`` returns the active :class:`SelfImprovementConfig`
  with secrets redacted.
- ``trigger_cycle`` runs an improvement cycle in-process and returns
  the produced proposals.

The two new live handlers fall back to ``capability_gap`` only when
``self_improvement_service`` is not wired on AppState, matching the
optional-service pattern other handlers (activity feed, agent health,
etc.) already use.
"""

import copy
from collections.abc import Mapping  # noqa: TC003 -- PEP 649 annotation
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.meta.rules.service import CustomRulesService

from synthorg.meta.errors import SelfImprovementTriggerError
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    capability_gap,
    err,
    ok,
    require_destructive_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import actor_id, coerce_pagination
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_DESTRUCTIVE_OP_EXECUTED,
    MCP_HANDLER_INVOKE_SUCCESS,
    MCP_HANDLER_LAZY_SERVICE_INIT,
)

logger = get_logger(__name__)


_WHY_SELF_IMPROVEMENT = (
    "self-improvement service is not wired on app_state in this "
    "deployment; enable the meta loop to use this tool"
)


def _custom_rules_service(app_state: Any) -> CustomRulesService:
    """Return the custom-rules service facade.

    Prefers ``app_state.custom_rules_service`` when bootstrap has wired
    one; otherwise builds it per-call from
    ``app_state.persistence.custom_rules`` and emits
    ``MCP_HANDLER_LAZY_SERVICE_INIT`` so ops telemetry sees legacy
    wiring.  The per-call fallback mirrors the controller layer in
    ``api.controllers.custom_rules`` and is retained so handlers keep
    working on ``AppState`` instances constructed before the
    ``custom_rules_service`` slot was added; new bootstraps should
    wire the service up front to skip the fallback log entirely.
    """
    cached = getattr(app_state, "custom_rules_service", None)
    if cached is not None:
        return cached  # type: ignore[no-any-return]
    logger.debug(
        MCP_HANDLER_LAZY_SERVICE_INIT,
        tool_name="meta._custom_rules_service",
        service="custom_rules_service",
        reason="app_state.custom_rules_service not wired -- building per-call",
    )
    from synthorg.meta.rules.service import CustomRulesService  # noqa: PLC0415

    return CustomRulesService(repo=app_state.persistence.custom_rules)


def _rule_to_dict(rule: Any) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "description": rule.description,
        "metric_path": rule.metric_path,
        "comparator": rule.comparator.value,
        "threshold": rule.threshold,
        "severity": rule.severity.value,
        "target_altitudes": [a.value for a in rule.target_altitudes],
        "enabled": rule.enabled,
        "created_at": rule.created_at.isoformat(),
        "updated_at": rule.updated_at.isoformat(),
    }


async def _meta_get_config(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_meta_get_config"
    if not getattr(app_state, "has_self_improvement_service", False):
        return capability_gap(tool, _WHY_SELF_IMPROVEMENT)
    try:
        config_dump = app_state.self_improvement_service.get_config()
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=config_dump)


async def _meta_list_rules(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_meta_list_rules"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        page, total = await _custom_rules_service(app_state).list_rules(
            offset=offset,
            limit=limit,
        )
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=[_rule_to_dict(r) for r in page], pagination=pagination)


async def _meta_list_mcp_tools(
    *,
    app_state: Any,  # noqa: ARG001
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_meta_list_mcp_tools"
    try:
        # Deferred import breaks the handlers->server->handlers import
        # cycle; kept inside the try so ImportError / circular-import
        # surfaces through the same error envelope as runtime failures.
        from synthorg.meta.mcp.server import get_registry  # noqa: PLC0415

        registry = get_registry()
        tools = list(registry.get_tool_definitions())
        response = ok(data=tools)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return response


async def _meta_get_mcp_server_config(
    *,
    app_state: Any,  # noqa: ARG001
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_meta_get_mcp_server_config"
    try:
        from synthorg.meta.mcp.server import get_server_config  # noqa: PLC0415

        config = get_server_config()
        response = ok(data=config)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return response


async def _meta_trigger_cycle(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    tool = "synthorg_meta_trigger_cycle"
    # Capability-gap check runs first so deployments that haven't wired
    # the self-improvement service surface the dedicated
    # ``capability_gap`` envelope, not a guardrail violation. The
    # destructive-op triple (identified actor + ``confirm=True`` +
    # non-blank ``reason``) is mandatory because this tool is declared
    # via ``admin_tool`` in ``meta/mcp/domains/meta.py``; we apply it
    # immediately after confirming the tool can actually execute.
    if not getattr(app_state, "has_self_improvement_service", False):
        return capability_gap(tool, _WHY_SELF_IMPROVEMENT)
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    actor_str = actor_id(resolved_actor) or "mcp"
    try:
        result = await app_state.self_improvement_service.trigger_cycle()
    except SelfImprovementTriggerError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="unavailable")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_DESTRUCTIVE_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=actor_str,
        reason=reason,
        target_id=str(result.cycle_id),
    )
    return ok(data=result.model_dump(mode="json"))


META_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    copy.deepcopy(
        {
            "synthorg_meta_get_config": _meta_get_config,
            "synthorg_meta_list_rules": _meta_list_rules,
            "synthorg_meta_list_mcp_tools": _meta_list_mcp_tools,
            "synthorg_meta_get_mcp_server_config": _meta_get_mcp_server_config,
            "synthorg_meta_trigger_cycle": _meta_trigger_cycle,
        },
    ),
)
