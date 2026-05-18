"""Provider-present switch: build the boot runtime services.

This is the construction site for the agent runtime. With a provider
configured it assembles ONE boot-time :class:`AgentEngine` (LLM +
sandboxed tools + memory, governed by the SecOps safety spine) and
shares that single engine between two consumers:

* an :class:`AgentEngineExecutionService` (the worker-callable execute
  seam), and
* a :class:`~synthorg.engine.coordination.service.MultiAgentCoordinator`
  built via :func:`~synthorg.engine.coordination.factory.build_coordinator`,
  whose :class:`~synthorg.engine.parallel.ParallelExecutor` runs sub-agents
  on the same engine.

With no provider it returns a :class:`NoProviderExecutionService` and a
``None`` coordinator, so the execute seam fails loudly and
``/coordinate`` honestly 503s instead of silently walking status
labels.

The same builder serves the boot install and the setup-reinit
rebuild, so configuring a provider brings the whole runtime (worker
execution AND multi-agent coordination) online without a process
restart.
"""

import asyncio
from typing import TYPE_CHECKING, NamedTuple

from synthorg.budget.baseline_store import BaselineStore
from synthorg.budget.coordination_collector import CoordinationMetricsCollector
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.coordination.factory import build_coordinator
from synthorg.engine.mcp_self_consumer import build_mcp_self_consumer
from synthorg.engine.routing.scorer import RoutingScorerConfig
from synthorg.engine.workspace.config import WorkspaceIsolationConfig
from synthorg.engine.workspace.git_worktree import PlannerWorktreeStrategy
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.security.action_types import ActionTypeRegistry
from synthorg.security.autonomy.resolver import AutonomyResolver
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import parse_int
from synthorg.tools.factory import build_default_tools_from_config
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.sandbox.factory import build_sandbox_backends
from synthorg.tools.sandbox.lifecycle.factory import create_lifecycle_strategy
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    NoProviderExecutionService,
    WorkerExecutionService,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from synthorg.api.state import AppState
    from synthorg.engine.coordination.service import MultiAgentCoordinator
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

_WEB_TIMEOUT_NS: str = "tools"
_WEB_TIMEOUT_KEY: str = "web_request_timeout_seconds"
_GIT_TIMEOUT_NS: str = "tools"
_GIT_TIMEOUT_KEY: str = "git_command_timeout_seconds"
_DECOMPOSITION_NS: str = "coordination"
_DECOMPOSITION_KEY: str = "decomposition_model"
_BASELINE_WINDOW_KEY: str = "baseline_window_size"


def _resolve_baseline_window_size() -> int:
    """Resolve ``budget.baseline_window_size`` at boot.

    Cat-2 boot knob (``read_only_post_init``): the ``BaselineStore``
    sliding window is sized once at construction, so the value is
    sourced env > registered default via the bootstrap resolver (a
    runtime change requires a restart). Mirrors ``app._resolve_budget_int``
    for ``coordination_metrics_max_entries``.
    """
    resolved = resolve_init_value(
        SettingNamespace.BUDGET,
        _BASELINE_WINDOW_KEY,
        parse=parse_int,
    )
    return int(resolved.value)


def _construct_coordination_collector(
    app_state: AppState,
) -> CoordinationMetricsCollector | None:
    """Build the shared coordination-metrics collector, or ``None``.

    Requires a live ``CostTracker`` (the collector's only non-optional
    dependency). Without one - the empty/degraded path - no collector
    is built and the metrics pipeline stays a no-op, mirroring the
    ``_construct_agent_engine`` optional-dependency guards. The single
    instance returned is threaded into both the single-agent
    ``AgentEngine`` and the multi-agent coordinator so one
    ``BaselineStore`` accumulates the single-agent baselines the
    multi-agent metrics compare against.
    """
    if not app_state.has_cost_tracker:
        return None
    baseline_store = BaselineStore(window_size=_resolve_baseline_window_size())
    return CoordinationMetricsCollector(
        config=app_state.config.coordination_metrics,
        cost_tracker=app_state.cost_tracker,
        message_bus=(app_state.message_bus if app_state.has_message_bus else None),
        baseline_store=baseline_store,
        metrics_store=(
            app_state.coordination_metrics_store
            if app_state.has_coordination_metrics_store
            else None
        ),
        clock=app_state.clock,
    )


class RuntimeServices(NamedTuple):
    """The pair of runtime services built behind the provider switch.

    INVARIANT (enforced by construction in :func:`build_runtime_services`,
    not by the type): when ``coordinator`` is not ``None`` it and
    ``worker_execution_service`` share the *same* boot
    :class:`AgentEngine` instance, so worker tasks and coordinator
    sub-agents observe one interrupt store, event-stream hub, and clock
    seam. A divergent engine would split agent state silently;
    ``tests/unit/workers/test_runtime_builder.py`` asserts the identity.
    ``coordinator`` is ``None`` only in the empty-company (no-provider)
    case, where ``worker_execution_service`` is a
    :class:`NoProviderExecutionService`.
    """

    worker_execution_service: WorkerExecutionService
    coordinator: MultiAgentCoordinator | None


def _select_active_provider(
    app_state: AppState,
) -> tuple[ProviderRegistry, tuple[str, ...]] | None:
    """Resolve the active provider registry, or ``None`` for empty-company.

    Logs the empty-company path and the unsupported multi-provider
    fan-in so the boot decision is observable.
    """
    if not app_state.has_active_provider:
        logger.info(
            API_APP_STARTUP,
            service="runtime_services",
            mode="no_provider",
            note="empty company -- task execution rejected at the seam",
        )
        return None

    registry = app_state.provider_registry
    names = registry.list_providers()
    if not names:
        logger.info(
            API_APP_STARTUP,
            service="runtime_services",
            mode="no_provider",
            note="provider registry present but empty",
        )
        return None
    if len(names) > 1:
        logger.warning(
            API_APP_STARTUP,
            service="runtime_services",
            note=(
                "multiple providers registered; the boot AgentEngine "
                "runs every agent against the first provider -- "
                "per-task multi-provider routing is not yet implemented"
            ),
            selected_provider=names[0],
            providers=list(names),
        )
    return registry, names


async def _build_tool_registry(
    app_state: AppState,
    workspace_root: Path,
) -> tuple[ToolRegistry, int, Mapping[str, SandboxBackend]]:
    """Create the sandbox workspace and the config-driven tool registry.

    Constructs the config-selected sandbox lifecycle strategy
    (per-agent / per-task / per-call) at the boot site with the
    application clock, builds the per-category sandbox backends with it
    injected, then wires the tool registry against those backends.  The
    backends mapping is returned so the execution service can release
    the lifecycle owner at the task boundary and shut backends down.
    """
    await asyncio.to_thread(
        workspace_root.mkdir,
        parents=True,
        exist_ok=True,
    )
    web_request_timeout = await app_state.config_resolver.get_float(
        _WEB_TIMEOUT_NS,
        _WEB_TIMEOUT_KEY,
    )
    lifecycle_strategy = create_lifecycle_strategy(
        app_state.config.sandboxing.docker.lifecycle,
        clock=app_state.clock,
    )
    sandbox_backends = build_sandbox_backends(
        config=app_state.config.sandboxing,
        workspace=workspace_root,
        lifecycle_strategy=lifecycle_strategy,
    )
    tools = build_default_tools_from_config(
        workspace=workspace_root,
        config=app_state.config,
        sandbox_backends=sandbox_backends,
        web_request_timeout=web_request_timeout,
    )
    return ToolRegistry(list(tools)), len(tools), sandbox_backends


def _construct_agent_engine(
    app_state: AppState,
    provider: CompletionProvider,
    registry: ProviderRegistry,
    tool_registry: ToolRegistry,
    coordination_metrics_collector: CoordinationMetricsCollector | None,
) -> AgentEngine:
    """Assemble the boot ``AgentEngine`` from live application state.

    A single instance is shared by the worker execution service and the
    coordinator's parallel executor so both consumers observe the same
    interrupt store, event stream hub, and clock seam. The same
    ``coordination_metrics_collector`` is shared too, so single-agent
    runs accumulate the baselines the multi-agent metrics compare
    against.
    """
    return AgentEngine(
        coordination_metrics_collector=coordination_metrics_collector,
        provider=provider,
        provider_registry=registry,
        tool_registry=tool_registry,
        cost_tracker=(app_state.cost_tracker if app_state.has_cost_tracker else None),
        task_engine=app_state.task_engine,
        approval_store=app_state.approval_store,
        approval_gate=app_state.approval_gate,
        trust_service=(
            app_state.trust_service if app_state.has_trust_service else None
        ),
        mcp_self_consumer=build_mcp_self_consumer(
            app_state.config.security.mcp_self_consumer,
            app_state,
        ),
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


async def _build_workspace_strategy(
    app_state: AppState,
) -> tuple[PlannerWorktreeStrategy, WorkspaceIsolationConfig]:
    """Build the git-worktree workspace isolation strategy + config.

    The strategy operates on ``app_state.agent_workspace_root`` (the
    same directory the worker runtime's sandbox tools use). Git
    subprocess invocations are bounded by the operator-tuned
    ``tools.git_command_timeout_seconds`` so a hung worktree command
    cannot stall a coordination wave. Construction (here, at boot) never
    touches git; a real repository is only required later, when a
    coordination wave first invokes ``workspace_service.setup_group()``
    during dispatch, and only when ``enable_workspace_isolation`` is set
    and the wave has multiple subtasks.
    """
    ws_config = WorkspaceIsolationConfig()
    git_timeout = await app_state.config_resolver.get_float(
        _GIT_TIMEOUT_NS,
        _GIT_TIMEOUT_KEY,
    )
    strategy = PlannerWorktreeStrategy(
        config=ws_config.planner_worktrees,
        repo_root=app_state.agent_workspace_root,
        cmd_timeout=git_timeout,
        clock=app_state.clock,
    )
    return strategy, ws_config


async def _resolve_routing_scorer_config(
    app_state: AppState,
) -> RoutingScorerConfig | None:
    """Project routing-scorer weights out of the engine bridge config.

    Fail-open: a bridge-resolution failure (missing setting, validation
    error, persistence flake) or a projection failure keeps the
    coordinator buildable by returning ``None`` so the factory falls
    back to ``task_assignment_config.min_score``. Mirrors the fail-open
    pattern used by ``auto_create_template_agents._resolve_matcher_config``
    and ``post_setup_reinit``. The resolve and projection stages are
    caught separately so the log says which one failed (a persistent
    config bug vs a transient resolver flake are diagnosed differently).
    """
    try:
        bridge = await app_state.config_resolver.get_engine_bridge_config()
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            service="coordinator",
            context="routing_scorer_config_resolve",
            note="engine bridge config unavailable; using scorer defaults",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    try:
        return RoutingScorerConfig.from_bridge_config(bridge)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            service="coordinator",
            context="routing_scorer_config_projection",
            note="scorer config projection failed; using scorer defaults",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


async def _build_runtime_coordinator(
    app_state: AppState,
    engine: AgentEngine,
    provider: CompletionProvider,
    coordination_metrics_collector: CoordinationMetricsCollector | None,
) -> MultiAgentCoordinator:
    """Build the multi-agent coordinator sharing the boot engine.

    Resolves the operator-tuned decomposition model and routing-scorer
    weights, wires real git-worktree workspace isolation, then delegates
    to the unit-tested :func:`build_coordinator` factory. The three
    resolution steps are independent, so they run concurrently under a
    ``TaskGroup`` to keep boot latency down (structured concurrency: any
    failure cancels the siblings and propagates).
    """
    async with asyncio.TaskGroup() as tg:
        model_task = tg.create_task(
            app_state.config_resolver.get_str(
                _DECOMPOSITION_NS,
                _DECOMPOSITION_KEY,
            )
        )
        scorer_task = tg.create_task(_resolve_routing_scorer_config(app_state))
        workspace_task = tg.create_task(_build_workspace_strategy(app_state))
    decomposition_model = model_task.result()
    routing_scorer_config = scorer_task.result()
    workspace_strategy, workspace_config = workspace_task.result()
    performance_tracker = (
        app_state.performance_tracker if app_state.has_performance_tracker else None
    )
    coordinator = build_coordinator(
        config=app_state.config.coordination,
        engine=engine,
        task_assignment_config=app_state.config.task_assignment,
        provider=provider,
        decomposition_model=decomposition_model,
        task_engine=app_state.task_engine,
        workspace_strategy=workspace_strategy,
        workspace_config=workspace_config,
        performance_tracker=performance_tracker,
        routing_scorer_config=routing_scorer_config,
        coordination_metrics_collector=coordination_metrics_collector,
    )
    logger.info(
        API_APP_STARTUP,
        service="coordinator",
        mode="multi_agent",
        decomposition_model=decomposition_model,
        topology=app_state.config.coordination.topology.value,
    )
    return coordinator


async def build_runtime_services(
    app_state: AppState,
    *,
    workspace_root: Path,
) -> RuntimeServices:
    """Return the runtime services for the current provider state.

    Args:
        app_state: Live application state (provider registry, task
            engine, approval store, security config, ...).
        workspace_root: Absolute filesystem root the agent's
            file-system / sandbox tools operate within. Resolved by the
            startup site (env-aware) and carried on ``AppState`` so the
            re-init path rebuilds against the same directory.

    Returns:
        ``RuntimeServices`` with an ``AgentEngineExecutionService`` and a
        live ``MultiAgentCoordinator`` (both sharing one
        ``AgentEngine``) when a provider is registered; otherwise a
        ``NoProviderExecutionService`` and a ``None`` coordinator.
    """
    selected = _select_active_provider(app_state)
    if selected is None:
        return RuntimeServices(
            worker_execution_service=NoProviderExecutionService(),
            coordinator=None,
        )
    registry, names = selected
    provider = registry.get(names[0])

    tool_registry, tool_count, sandbox_backends = await _build_tool_registry(
        app_state,
        workspace_root,
    )
    coordination_metrics_collector = _construct_coordination_collector(app_state)
    engine = _construct_agent_engine(
        app_state,
        provider,
        registry,
        tool_registry,
        coordination_metrics_collector,
    )
    autonomy_resolver = AutonomyResolver(
        registry=ActionTypeRegistry(),
        config=app_state.config.config.autonomy,
    )
    coordinator = await _build_runtime_coordinator(
        app_state,
        engine,
        provider,
        coordination_metrics_collector,
    )
    logger.info(
        API_APP_STARTUP,
        service="runtime_services",
        mode="agent_engine",
        provider=names[0],
        tool_count=tool_count,
    )
    worker_execution_service = AgentEngineExecutionService(
        engine=engine,
        task_engine=app_state.task_engine,
        agent_registry=app_state.agent_registry,
        autonomy_resolver=autonomy_resolver,
        sandbox_backend=sandbox_backends.get("docker"),
        lifecycle_strategy_kind=(app_state.config.sandboxing.docker.lifecycle.strategy),
    )
    return RuntimeServices(
        worker_execution_service=worker_execution_service,
        coordinator=coordinator,
    )
