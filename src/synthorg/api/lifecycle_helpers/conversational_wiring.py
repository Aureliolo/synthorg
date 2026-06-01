# module-kind: code
"""On-startup wiring for the conversational write-path services.

Wires the multi-agent group chat behind its feature flag +
runtime dependencies. Lifted out of :mod:`feature_wiring` so the
conversational write-path wirers (the direct-MCP actor wirer joins
here) stay cohesive and ``feature_wiring`` remains a thin dispatcher
under its size tier.
"""

from typing import TYPE_CHECKING

from synthorg.api.conversational_builders import (
    build_conversational_actor,
    build_group_chat_service,
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.budget.tracker import CostTracker
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)


async def wire_group_chat_service(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    persistence: PersistenceBackend | None,
    cost_tracker: CostTracker | None,
) -> None:
    """Wire the multi-agent group chat behind group_chat_enabled + deps.

    Returns ``None`` (leaving ``POST /meta/chat/group`` at 503) when the
    flag is off, no provider/agent registry is present, or persistence
    is absent. Idempotent: a second boot pass skips when already wired.
    """
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.persistence.conversational_factory import (  # noqa: PLC0415
        build_conversational_repositories,
    )
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).group_chat_service is not None:
        return
    agent_registry = app_state.slice(HrStateSlice).agent_registry
    if provider_registry is None or agent_registry is None:
        return
    meta_self_improvement = await load_self_improvement_config(
        app_state.slice(SettingsStateSlice).settings_service,
    )
    repositories = build_conversational_repositories(persistence)
    service = build_group_chat_service(
        meta_self_improvement.chief_of_staff,
        provider_registry=provider_registry,
        agent_registry=agent_registry,
        repositories=repositories,
        cost_tracker=cost_tracker,
        approval_store=app_state.slice(ApprovalStateSlice).store,
    )
    if service is not None:
        app_state.wire(MetaStateSlice, group_chat_service=service)
        logger.info(
            API_APP_STARTUP,
            service="group_chat_service",
            note="group chat wired",
        )


async def wire_conversational_actor(app_state: AppState) -> None:
    """Wire the direct-MCP conversational actor behind direct_mcp_enabled.

    Reuses the SHARED boot ``AgentEngine`` (held by the
    ``AgentEngineExecutionService``) so a sensitive chat action parks on
    the same ``ApprovalGate`` the ``/approvals`` controller resumes.
    Returns ``None`` -- leaving ``POST /meta/chat/act`` at 503 -- when the
    flag is off, no agent registry is present, or no provider-backed boot
    engine was installed (empty company). Idempotent: a second boot pass
    skips when already wired.
    """
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.meta.config import load_self_improvement_config  # noqa: PLC0415
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415
    from synthorg.workers.execution_service import (  # noqa: PLC0415
        AgentEngineExecutionService,
    )
    from synthorg.workers.state import RuntimeStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).conversational_actor is not None:
        return
    agent_registry = app_state.slice(HrStateSlice).agent_registry
    if agent_registry is None:
        return
    # Read the slice directly (not ``worker_execution_service_of``) so a
    # not-yet-installed service stays ``None`` rather than lazily building
    # the lifecycle-only baseline; only a real boot engine can act.
    service = app_state.slice(RuntimeStateSlice).worker_execution_service
    if not isinstance(service, AgentEngineExecutionService):
        return
    meta_self_improvement = await load_self_improvement_config(
        app_state.slice(SettingsStateSlice).settings_service,
    )
    actor = build_conversational_actor(
        meta_self_improvement.chief_of_staff,
        engine=service.engine,
        agent_registry=agent_registry,
        autonomy_resolver=service.autonomy_resolver,
    )
    if actor is not None:
        app_state.wire(MetaStateSlice, conversational_actor=actor)
        logger.info(
            API_APP_STARTUP,
            service="conversational_actor",
            note="direct MCP actor wired",
        )


__all__ = ["wire_conversational_actor", "wire_group_chat_service"]
