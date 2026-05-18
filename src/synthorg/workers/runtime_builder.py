"""Provider-present switch: build the worker execution service.

This is the construction site for the agent runtime. With a provider
configured it assembles one boot-time :class:`AgentEngine` (LLM +
sandboxed tools + memory, governed by the SecOps safety spine) wrapped
in an :class:`AgentEngineExecutionService`. With no provider it returns
an :class:`NoProviderExecutionService` so the execute seam fails loudly
instead of silently walking status labels.

The same builder serves the boot install and the setup-reinit
rebuild, so configuring a provider brings the runtime online without
a process restart.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.engine.agent_engine import AgentEngine
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.security.action_types import ActionTypeRegistry
from synthorg.security.autonomy.models import AutonomyConfig
from synthorg.security.autonomy.resolver import AutonomyResolver
from synthorg.tools.factory import build_default_tools_from_config
from synthorg.tools.registry import ToolRegistry
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    NoProviderExecutionService,
    WorkerExecutionService,
)

if TYPE_CHECKING:
    from pathlib import Path

    from synthorg.api.state import AppState

logger = get_logger(__name__)

_WEB_TIMEOUT_NS: str = "tools"
_WEB_TIMEOUT_KEY: str = "web_request_timeout_seconds"


async def build_worker_execution_service(
    app_state: AppState,
    *,
    workspace_root: Path,
) -> WorkerExecutionService:
    """Return the worker execution service for the current provider state.

    Args:
        app_state: Live application state (provider registry, task
            engine, approval store, security config, ...).
        workspace_root: Absolute filesystem root the agent's
            file-system / sandbox tools operate within. Resolved by the
            startup site (env-aware) and carried on ``AppState`` so the
            re-init path rebuilds against the same directory.

    Returns:
        ``AgentEngineExecutionService`` when a provider is registered,
        otherwise ``NoProviderExecutionService``.
    """
    if not app_state.has_active_provider:
        logger.info(
            API_APP_STARTUP,
            service="worker_execution_service",
            mode="no_provider",
            note="empty company -- task execution rejected at the seam",
        )
        return NoProviderExecutionService()

    registry = app_state.provider_registry
    names = registry.list_providers()
    if not names:
        logger.info(
            API_APP_STARTUP,
            service="worker_execution_service",
            mode="no_provider",
            note="provider registry present but empty",
        )
        return NoProviderExecutionService()
    if len(names) > 1:
        logger.warning(
            API_APP_STARTUP,
            service="worker_execution_service",
            note=(
                "multiple providers registered; the boot AgentEngine "
                "runs every agent against the first provider -- "
                "per-task multi-provider routing is not yet implemented"
            ),
            selected_provider=names[0],
            providers=list(names),
        )
    provider = registry.get(names[0])

    await asyncio.to_thread(
        workspace_root.mkdir,
        parents=True,
        exist_ok=True,
    )
    web_request_timeout = await app_state.config_resolver.get_float(
        _WEB_TIMEOUT_NS,
        _WEB_TIMEOUT_KEY,
    )
    tools = build_default_tools_from_config(
        workspace=workspace_root,
        config=app_state.config,
        web_request_timeout=web_request_timeout,
    )
    tool_registry = ToolRegistry(list(tools))

    engine = AgentEngine(
        provider=provider,
        provider_registry=registry,
        tool_registry=tool_registry,
        cost_tracker=(app_state.cost_tracker if app_state.has_cost_tracker else None),
        task_engine=app_state.task_engine,
        approval_store=app_state.approval_store,
        security_config=app_state.config.security,
        audit_log=app_state.audit_log if app_state.has_audit_log else None,
        memory_backend=(
            app_state.memory_backend if app_state.has_memory_backend else None
        ),
        config_resolver=app_state.config_resolver,
        event_stream_hub=app_state.event_stream_hub,
        interrupt_store=app_state.interrupt_store,
        clock=app_state.clock,
    )
    autonomy_resolver = AutonomyResolver(
        registry=ActionTypeRegistry(),
        config=AutonomyConfig(),
    )
    logger.info(
        API_APP_STARTUP,
        service="worker_execution_service",
        mode="agent_engine",
        provider=names[0],
        tool_count=len(tools),
    )
    return AgentEngineExecutionService(
        engine=engine,
        task_engine=app_state.task_engine,
        agent_registry=app_state.agent_registry,
        autonomy_resolver=autonomy_resolver,
    )
