# module-kind: code
"""Coordinator and work-pipeline assembly for the runtime-services builder.

Owns the coordination-side construction steps behind
:func:`synthorg.workers.runtime_builder.build_runtime_services`: the
workspace-isolation strategy, the routing-scorer config projection, the
multi-agent coordinator, and the work-pipeline spine.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.budget.coordination_collector import CoordinationMetricsCollector
from synthorg.budget.state import BudgetStateSlice
from synthorg.client.state import client_simulation_state_of, has_simulation_runtime
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.middleware_config import CoordinationMiddlewareConfig
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.coordination.factory import build_coordinator
from synthorg.engine.middleware._defaults import register_coordination_defaults
from synthorg.engine.middleware.factory import build_coordination_middleware_chain
from synthorg.engine.middleware.replan_factory import create_replan_hook
from synthorg.engine.pipeline.factory import build_work_pipeline
from synthorg.engine.routing.scorer import AgentTaskScorer, RoutingScorerConfig
from synthorg.engine.state import task_engine_of
from synthorg.engine.workspace.config import WorkspaceIsolationConfig
from synthorg.engine.workspace.git_worktree import PlannerWorktreeStrategy
from synthorg.engine.workspace.state import (
    WorkspaceStateSlice,
    agent_workspace_root_of,
)
from synthorg.hr.state import HrStateSlice, agent_registry_of
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.state import persistence_of
from synthorg.settings.state import config_resolver_of
from synthorg.workers.execution_service import WorkerExecutionService

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.engine.coordination.service import MultiAgentCoordinator
    from synthorg.engine.middleware.coordination_protocol import (
        CoordinationMiddlewareChain,
    )
    from synthorg.engine.pipeline.protocol import WorkPipeline
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)

_GIT_TIMEOUT_NS: str = "tools"
_GIT_TIMEOUT_KEY: str = "git_command_timeout_seconds"
_DECOMPOSITION_NS: str = "coordination"
_DECOMPOSITION_KEY: str = "decomposition_model"
_ROUTING_POLICY_KEY: str = "routing_policy"
_LEAF_THRESHOLD_KEY: str = "leaf_subtask_threshold"


async def _build_workspace_strategy(
    app_state: AppState,
) -> tuple[PlannerWorktreeStrategy, WorkspaceIsolationConfig]:
    """Build the git-worktree workspace isolation strategy + config.

    The strategy operates on the workspace slice's
    ``agent_workspace_root`` (the same directory the worker runtime's
    sandbox tools use). Git
    subprocess invocations are bounded by the operator-tuned
    ``tools.git_command_timeout_seconds`` so a hung worktree command
    cannot stall a coordination wave. Construction (here, at boot) never
    touches git; a real repository is only required later, when a
    coordination wave first invokes ``workspace_service.setup_group()``
    during dispatch, and only when ``enable_workspace_isolation`` is set
    and the wave has multiple subtasks.

    Returns:
        A ``(strategy, config)`` pair: the git-worktree isolation
        strategy and its workspace-isolation config.
    """
    ws_config = WorkspaceIsolationConfig()
    git_timeout = await config_resolver_of(app_state).get_float(
        _GIT_TIMEOUT_NS,
        _GIT_TIMEOUT_KEY,
    )
    strategy = PlannerWorktreeStrategy(
        config=ws_config.planner_worktrees,
        repo_root=agent_workspace_root_of(app_state),
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

    Returns:
        The projected ``RoutingScorerConfig``, or ``None`` (fail-open) so
        the factory falls back to ``task_assignment_config.min_score``.
    """
    try:
        bridge = await config_resolver_of(app_state).get_engine_bridge_config()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
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
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="coordinator",
            context="routing_scorer_config_projection",
            note="scorer config projection failed; using scorer defaults",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


async def _resolve_coordinator_dependencies(
    app_state: AppState,
) -> tuple[
    str,
    RoutingScorerConfig | None,
    tuple[PlannerWorktreeStrategy, WorkspaceIsolationConfig],
]:
    """Resolve decomposition model, routing-scorer config, and workspace concurrently.

    The three resolution steps are independent, so they run under a
    ``TaskGroup`` to keep boot latency down (structured concurrency: any
    failure cancels the siblings and propagates).

    Returns:
        A ``(decomposition_model, routing_scorer_config, (workspace_strategy,
        workspace_config))`` triple.
    """
    try:
        async with asyncio.TaskGroup() as tg:
            model_task = tg.create_task(
                config_resolver_of(app_state).get_str(
                    _DECOMPOSITION_NS,
                    _DECOMPOSITION_KEY,
                )
            )
            scorer_task = tg.create_task(_resolve_routing_scorer_config(app_state))
            workspace_task = tg.create_task(_build_workspace_strategy(app_state))
    except Exception as exc:
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            API_APP_STARTUP,
            exc,
            service="coordinator",
            context="resolve_failed",
            note="decomposition / routing-scorer / workspace config resolve failed",
        )
        raise
    return (
        model_task.result(),
        scorer_task.result(),
        workspace_task.result(),
    )


def _build_coordination_chain(
    app_state: AppState,
) -> CoordinationMiddlewareChain | None:
    """Build the coordination middleware chain, or ``None`` when disabled.

    Gated on ``coordination.enable_coordination_middleware`` (off by
    default, so wiring the pipeline in preserves current behaviour
    exactly). When enabled, registers the default middleware factories,
    builds the configured replan hook via the ``replan_strategy``
    discriminator (``noop`` is the safe default), and composes the
    default coordination chain. The shared :class:`BudgetEnforcer` on the
    budget slice (``None`` on a persistence-less boot) gates an affordable
    magentic replan.

    Returns:
        The composed :class:`CoordinationMiddlewareChain`, or ``None``
        when the pipeline is disabled.
    """
    coord_section = app_state.config.coordination
    if not coord_section.enable_coordination_middleware:
        return None
    register_coordination_defaults()
    replan_hook = create_replan_hook(
        coord_section.replan_strategy,
        max_stall_count=coord_section.max_stall_count,
        max_reset_count=coord_section.max_reset_count,
        budget_enforcer=app_state.slice(BudgetStateSlice).budget_enforcer,
    )
    return build_coordination_middleware_chain(
        CoordinationMiddlewareConfig(),
        deps={"replan_hook": replan_hook},
    )


async def _build_runtime_coordinator(
    app_state: AppState,
    engine: AgentEngine,
    provider: CompletionProvider,
    coordination_metrics_collector: CoordinationMetricsCollector | None,
) -> tuple[MultiAgentCoordinator, AgentTaskScorer, str]:
    """Build the coordinator and the shared scorer + decomposition model.

    Resolves the operator-tuned decomposition model and routing-scorer
    weights, wires real git-worktree workspace isolation, then delegates
    to the unit-tested :func:`build_coordinator` factory. The three
    resolution steps are independent, so they run concurrently under a
    ``TaskGroup`` to keep boot latency down (structured concurrency: any
    failure cancels the siblings and propagates). The ``AgentTaskScorer``
    is constructed here and injected into the coordinator so the work
    pipeline's solo-path selection can share the very same instance
    (one routing surface, no divergence). The resolved decomposition
    model is returned so the ``llm-judged`` routing policy reuses it.

    Returns:
        A ``(coordinator, scorer, decomposition_model)`` triple sharing
        the boot engine and a single ``AgentTaskScorer``.
    """
    (
        decomposition_model,
        routing_scorer_config,
        (workspace_strategy, workspace_config),
    ) = await _resolve_coordinator_dependencies(app_state)
    performance_tracker = app_state.slice(HrStateSlice).performance_tracker
    if routing_scorer_config is None:
        scorer = AgentTaskScorer(min_score=app_state.config.task_assignment.min_score)
    else:
        scorer = AgentTaskScorer(config=routing_scorer_config)
    project_workspace_service = app_state.slice(
        WorkspaceStateSlice
    ).project_workspace_service
    git_backend = (
        project_workspace_service.git_backend
        if project_workspace_service is not None
        else None
    )
    # ``AgentEngineExecutionService`` provisions the per-project workspace
    # lazily on first task; bare construction (no service) keeps the
    # persistence-less dev paths working as before.
    coordinator = build_coordinator(
        config=app_state.config.coordination,
        engine=engine,
        task_assignment_config=app_state.config.task_assignment,
        provider=provider,
        decomposition_model=decomposition_model,
        task_engine=task_engine_of(app_state),
        workspace_strategy=workspace_strategy,
        workspace_config=workspace_config,
        project_workspace_service=project_workspace_service,
        git_backend=git_backend,
        performance_tracker=performance_tracker,
        routing_scorer_config=routing_scorer_config,
        coordination_metrics_collector=coordination_metrics_collector,
        scorer=scorer,
        coordination_chain=_build_coordination_chain(app_state),
        shutdown_manager=app_state.shutdown_manager,
    )
    logger.info(
        API_APP_STARTUP,
        service="coordinator",
        mode="multi_agent",
        decomposition_model=decomposition_model,
        topology=app_state.config.coordination.topology.value,
    )
    return coordinator, scorer, decomposition_model


async def _build_runtime_work_pipeline(  # noqa: PLR0913 -- keyword-only DI
    app_state: AppState,
    *,
    scorer: AgentTaskScorer,
    coordinator: MultiAgentCoordinator,
    worker_execution_service: WorkerExecutionService,
    provider: CompletionProvider,
    decomposition_model: str,
) -> WorkPipeline | None:
    """Build the work pipeline spine, or ``None`` when no intake is wired.

    The spine consumes the boot ``IntakeEngine`` wired by the
    client-simulation runtime (the only work-entry path online today);
    without it there is no intake stage, so the pipeline stays
    unconfigured and ``/`` work routing honestly reports unavailability
    rather than silently dropping work. The solo-vs-team routing policy
    discriminator and leaf threshold are resolved at boot so the
    setting-to-startup trace holds.

    Returns:
        The wired ``WorkPipeline``, or ``None`` when no intake runtime is
        available (no work-entry path).
    """
    if not has_simulation_runtime(app_state):
        logger.info(
            API_APP_STARTUP,
            service="work_pipeline",
            mode="disabled",
            note="no intake runtime wired; work spine unavailable",
        )
        return None
    intake_engine = client_simulation_state_of(app_state).intake_engine
    if intake_engine is None:
        logger.info(
            API_APP_STARTUP,
            service="work_pipeline",
            mode="disabled",
            note="simulation runtime present but intake engine unset",
        )
        return None
    routing_policy = await config_resolver_of(app_state).get_str(
        _DECOMPOSITION_NS,
        _ROUTING_POLICY_KEY,
    )
    leaf_threshold = await config_resolver_of(app_state).get_int(
        _DECOMPOSITION_NS,
        _LEAF_THRESHOLD_KEY,
    )
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    return build_work_pipeline(
        intake_engine=intake_engine,
        task_engine=task_engine_of(app_state),
        project_repository=persistence_of(app_state).projects,
        scorer=scorer,
        worker_execution_service=worker_execution_service,
        coordinator=coordinator,
        agent_registry=agent_registry_of(app_state),
        routing_discriminator=routing_policy,
        leaf_threshold=leaf_threshold,
        provider=provider,
        decomposition_model=decomposition_model,
        cost_tracker=cost_tracker,
        clock=app_state.clock,
    )
