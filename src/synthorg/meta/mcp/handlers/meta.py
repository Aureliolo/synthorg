"""Meta (self-improvement) domain MCP handlers.

Five tools:

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

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.meta.rules.service import CustomRulesService

if TYPE_CHECKING:
    from synthorg.api.state import AppState

from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.errors import SelfImprovementTriggerError
from synthorg.meta.mcp.domains._simple_args import (
    MetaListRulesArgs,
    MetaQueryFeatureMapArgs,
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
    capability_gap,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import actor_id
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.meta.rules.custom import CustomRuleResponse
from synthorg.meta.state import MetaStateSlice, self_improvement_service_of
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_INVOKE_SUCCESS,
    MCP_HANDLER_LAZY_SERVICE_INIT,
)
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)


_WHY_SELF_IMPROVEMENT = (
    "self-improvement service is not wired on app_state in this "
    "deployment; enable the meta loop to use this tool"
)


def _custom_rules_service(app_state: AppState) -> CustomRulesService:
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

    Returns:
        ``CustomRulesService`` instance.
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
    return CustomRulesService(repo=persistence_of(app_state).custom_rules)


async def _meta_get_config(
    *,
    app_state: AppState,
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_meta_get_config`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_meta_get_config"
    if app_state.slice(MetaStateSlice).self_improvement_service is None:
        return capability_gap(tool, _WHY_SELF_IMPROVEMENT)
    try:
        config_dump = self_improvement_service_of(app_state).get_config()
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=config_dump)


async def _meta_list_rules(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_meta_list_rules`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_meta_list_rules"
    try:
        page_args = typed_args(arguments, MetaListRulesArgs)
        offset, limit = page_args.offset, page_args.limit
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        page, total = await _custom_rules_service(app_state).list_rules(
            offset=offset,
            limit=limit,
        )
        serialized = [
            CustomRuleResponse.from_definition(r).model_dump(mode="json") for r in page
        ]
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=serialized, pagination=pagination)


async def _meta_list_mcp_tools(
    *,
    app_state: AppState,  # noqa: ARG001
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_meta_list_mcp_tools`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_meta_list_mcp_tools"
    try:
        # Deferred import breaks the handlers->server->handlers import
        # cycle; kept inside the try so ImportError / circular-import
        # surfaces through the same error envelope as runtime failures.
        from synthorg.meta.mcp.server import get_registry  # noqa: PLC0415

        registry = get_registry()
        tools = list(registry.get_tool_definitions())
        response = ok(data=tools)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return response


async def _meta_get_mcp_server_config(
    *,
    app_state: AppState,  # noqa: ARG001
    arguments: dict[str, object],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_meta_get_mcp_server_config`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_meta_get_mcp_server_config"
    try:
        from synthorg.meta.mcp.server import get_server_config  # noqa: PLC0415

        config = get_server_config()
        response = ok(data=config)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return response


async def _meta_trigger_cycle(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_meta_trigger_cycle`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_meta_trigger_cycle"
    # Guardrail runs first so an unauthenticated caller never learns
    # whether the self-improvement service is installed: the wire
    # surface stays a pure ``guardrail_violated`` for missing actor /
    # confirm / reason; only authenticated callers see the
    # ``capability_gap`` envelope when the service isn't wired.
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        if app_state.slice(MetaStateSlice).self_improvement_service is None:
            return capability_gap(tool, _WHY_SELF_IMPROVEMENT)
        result = await self_improvement_service_of(app_state).trigger_cycle()
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except SelfImprovementTriggerError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="unavailable")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    actor_str = actor_id(resolved_actor) or "mcp"
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=actor_str,
        reason=reason,
        target_id=str(result.cycle_id),
    )
    return ok(data=result.model_dump(mode="json"))


async def _meta_query_feature_map(
    *,
    app_state: AppState,  # noqa: ARG001
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_meta_query_feature_map`` MCP tool.

    Returns:
        JSON-encoded MCP envelope carrying a :class:`FeatureIndex` dump.
        When ``arguments["name"]`` is set, the index contains 0 or 1 entry;
        otherwise it contains every discovered feature.
    """
    tool = "synthorg_meta_query_feature_map"
    try:
        name_filter = typed_args(arguments, MetaQueryFeatureMapArgs).name
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        # Deferred to avoid hauling the index builder into the handler-import
        # graph before the runtime is ready.
        from datetime import UTC, datetime  # noqa: PLC0415

        from synthorg._core.features import (  # noqa: PLC0415
            discover_features,
            feature_directories,
        )
        from synthorg.core.feature_map import (  # noqa: PLC0415
            FEATURE_INDEX_SCHEMA_VERSION,
            FeatureIndex,
            build_feature_map,
        )

        directories = feature_directories()
        maps = tuple(
            build_feature_map(feature, directories.get(feature.name, ""))
            for feature in sorted(discover_features(), key=lambda f: f.name)
            if name_filter is None or feature.name == name_filter
        )
        index = FeatureIndex(
            schema_version=FEATURE_INDEX_SCHEMA_VERSION,
            generated_at=datetime.now(UTC),
            features=maps,
        )
        payload = index.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=payload)


META_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_meta_get_config": _meta_get_config,
        "synthorg_meta_list_rules": _meta_list_rules,
        "synthorg_meta_list_mcp_tools": _meta_list_mcp_tools,
        "synthorg_meta_get_mcp_server_config": _meta_get_mcp_server_config,
        "synthorg_meta_trigger_cycle": _meta_trigger_cycle,
        "synthorg_meta_query_feature_map": _meta_query_feature_map,
    },
)
