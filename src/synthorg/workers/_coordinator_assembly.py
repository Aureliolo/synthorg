# module-kind: code
"""Coordinator and work-pipeline assembly for the runtime-services builder.

Owns the coordination-side construction steps behind
:func:`synthorg.workers.runtime_builder.build_runtime_services`: the
workspace-isolation strategy, the routing-scorer config projection, the
multi-agent coordinator, and the work-pipeline spine.
"""

import asyncio
from typing import TYPE_CHECKING, NamedTuple

from synthorg.budget.coordination_collector import CoordinationMetricsCollector
from synthorg.budget.session_budget import (
    SessionCeilings,
    resolve_session_token_ceiling,
)
from synthorg.budget.state import BudgetStateSlice
from synthorg.client.state import client_simulation_state_of, has_simulation_runtime
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.middleware_config import CoordinationMiddlewareConfig
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.coordination.factory import (
    CoordinatorRoutingDeps,
    build_coordinator,
)
from synthorg.engine.decomposition.agent_session import (
    AgentSessionDecompositionConfig,
)
from synthorg.engine.decomposition.planning_tool_provider import PlanningToolProvider
from synthorg.engine.errors import CoordinationConfigError
from synthorg.engine.middleware._defaults import register_coordination_defaults
from synthorg.engine.middleware.factory import build_coordination_middleware_chain
from synthorg.engine.pipeline.factory import (
    build_solo_assignment_service,
    build_work_pipeline,
)
from synthorg.engine.pipeline.policy import build_work_routing_policy
from synthorg.engine.roster import ServiceabilityFilteredRoster
from synthorg.engine.routing.scorer import AgentTaskScorer, RoutingScorerConfig
from synthorg.engine.state import task_engine_of
from synthorg.engine.workspace.config import WorkspaceIsolationConfig
from synthorg.engine.workspace.disk_quota import DiskQuotaWatcher
from synthorg.engine.workspace.git_worktree import PlannerWorktreeStrategy
from synthorg.engine.workspace.semantic_analyzer import AstSemanticAnalyzer
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
from synthorg.providers.agent_availability import ServiceabilityAvailabilityReader
from synthorg.providers.model_binding import resolve_ref_provider

# Module-level (not TYPE_CHECKING): the ``_owner_provider_selector`` closure
# carries a runtime-evaluated ``-> CompletionProvider`` annotation that typeguard
# resolves in this module's globals when the coordinator calls the selector.
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.model_ref import parse_model_ref
from synthorg.settings.state import config_resolver_of
from synthorg.tools.web.providers.http_search_provider import HttpWebSearchProvider
from synthorg.workers._capability_policy_wiring import build_capability_policy
from synthorg.workers._planning_memory import (
    PlanningMemoryGrant,
    build_planning_memory,
)
from synthorg.workers.execution_service import WorkerExecutionService

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.engine.coordination.service import MultiAgentCoordinator
    from synthorg.engine.middleware.coordination_protocol import (
        CoordinationMiddlewareChain,
    )
    from synthorg.engine.pipeline.protocol import WorkPipeline

logger = get_logger(__name__)

_GIT_TIMEOUT_NS: str = "tools"
_GIT_TIMEOUT_KEY: str = "git_command_timeout_seconds"
_DECOMPOSITION_NS: str = "coordination"
_DECOMPOSITION_KEY: str = "decomposition_model"
_DECOMPOSITION_STRATEGY_KEY: str = "decomposition_strategy"
_DECOMPOSITION_AGENT_MAX_TURNS_KEY: str = "decomposition_agent_max_turns"
_DECOMPOSITION_AGENT_COST_CEILING_KEY: str = "decomposition_agent_cost_ceiling"
_MIDDLEWARE_KEY: str = "enable_coordination_middleware"
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
        semantic_analyzer=AstSemanticAnalyzer(
            config=ws_config.planner_worktrees.semantic_analysis,
        ),
        disk_quota_watcher=DiskQuotaWatcher(ws_config.planner_worktrees),
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
        # lint-allow: swallow-ok -- degrade-to-None wiring
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
        # lint-allow: swallow-ok -- degrade-to-None wiring
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


class _CoordinatorDependencies(NamedTuple):
    """Everything the coordinator build reads from settings and slices.

    Named rather than an eight-element tuple unpacked by position: the
    members are unrelated to each other, so an insertion in the middle
    silently reassigns every field after it at the one call site.

    Attributes:
        decomposition_ref: Raw ``coordination.decomposition_model`` value.
        decomposition_strategy: Strategy discriminator.
        agent_session_max_turns: Turn cap for the planning session.
        agent_session_ceilings: Both spend bounds on that session.
        routing_scorer_config: Projected scorer weights, or ``None``.
        workspace: The worktree strategy and its isolation config.
        middleware_enabled: Whether the coordination chain is built.
        planning_memory: The planning session's memory grant.
    """

    decomposition_ref: str
    decomposition_strategy: str
    agent_session_max_turns: int
    agent_session_ceilings: SessionCeilings
    routing_scorer_config: RoutingScorerConfig | None
    workspace: tuple[PlannerWorktreeStrategy, WorkspaceIsolationConfig]
    middleware_enabled: bool
    planning_memory: PlanningMemoryGrant


async def _resolve_coordinator_dependencies(
    app_state: AppState,
) -> _CoordinatorDependencies:
    """Resolve decomposition model/strategy, session tuning, scorer, workspace.

    The resolution steps are independent, so they run under a
    ``TaskGroup`` to keep boot latency down (structured concurrency: any
    failure cancels the siblings and propagates). The agent-session turn cap
    and spend ceilings, the middleware-enabled flag, and the planning memory
    grant are resolved here too so every remote read happens in one group
    rather than serial tail reads at the build site.

    Returns:
        The resolved :class:`_CoordinatorDependencies`.
    """
    try:
        async with asyncio.TaskGroup() as tg:
            resolver = config_resolver_of(app_state)
            model_task = tg.create_task(
                resolver.get_str(
                    _DECOMPOSITION_NS,
                    _DECOMPOSITION_KEY,
                )
            )
            strategy_task = tg.create_task(
                resolver.get_str(
                    _DECOMPOSITION_NS,
                    _DECOMPOSITION_STRATEGY_KEY,
                )
            )
            max_turns_task = tg.create_task(
                resolver.get_int(
                    _DECOMPOSITION_NS,
                    _DECOMPOSITION_AGENT_MAX_TURNS_KEY,
                )
            )
            cost_ceiling_task = tg.create_task(
                resolver.get_float(
                    _DECOMPOSITION_NS,
                    _DECOMPOSITION_AGENT_COST_CEILING_KEY,
                )
            )
            token_ceiling_task = tg.create_task(resolve_session_token_ceiling(resolver))
            scorer_task = tg.create_task(_resolve_routing_scorer_config(app_state))
            workspace_task = tg.create_task(_build_workspace_strategy(app_state))
            middleware_task = tg.create_task(
                resolver.get_bool(
                    _DECOMPOSITION_NS,
                    _MIDDLEWARE_KEY,
                )
            )
            # A plan should build on what the organisation already learned, so
            # the planning session recalls past retros, org playbooks, and the
            # owner's prior-initiative memory.
            planning_task = tg.create_task(build_planning_memory(app_state))
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
    return _CoordinatorDependencies(
        decomposition_ref=model_task.result(),
        decomposition_strategy=strategy_task.result(),
        agent_session_max_turns=max_turns_task.result(),
        agent_session_ceilings=SessionCeilings.of(
            cost_ceiling=cost_ceiling_task.result(),
            token_ceiling=token_ceiling_task.result(),
        ),
        routing_scorer_config=scorer_task.result(),
        workspace=workspace_task.result(),
        middleware_enabled=middleware_task.result(),
        planning_memory=planning_task.result(),
    )


def _build_coordination_chain(
    *,
    enabled: bool,
) -> CoordinationMiddlewareChain | None:
    """Build the coordination middleware chain, or ``None`` when disabled.

    Gated on *enabled* (resolved by the caller from the
    ``coordination.enable_coordination_middleware`` setting, on by
    default). When enabled, registers the default middleware factories
    and composes the default coordination chain.

    Args:
        enabled: Whether the middleware pipeline is enabled (resolved from
            the setting, DB > env > default).

    Returns:
        The composed :class:`CoordinationMiddlewareChain`, or ``None``
        when the pipeline is disabled.
    """
    if not enabled:
        return None
    register_coordination_defaults()
    return build_coordination_middleware_chain(
        CoordinationMiddlewareConfig(),
        deps={},
    )


async def _build_runtime_coordinator(
    app_state: AppState,
    engine: AgentEngine,
    coordination_metrics_collector: CoordinationMetricsCollector | None,
    *,
    search_provider: HttpWebSearchProvider | None = None,
) -> tuple[MultiAgentCoordinator, AgentTaskScorer, CompletionProvider, str]:
    """Build the coordinator and the shared scorer + decomposition binding.

    Resolves the operator-tuned decomposition model reference, agent-session
    turn cap + spend ceiling, and routing-scorer weights, wires real
    git-worktree workspace isolation, then delegates to the unit-tested
    :func:`build_coordinator` factory. The resolution steps are independent, so
    they run concurrently under a ``TaskGroup`` to keep boot latency down
    (structured concurrency: any failure cancels the siblings and
    propagates). The ``AgentTaskScorer``
    is constructed here and injected into the coordinator so the work
    pipeline's solo-path selection can share the very same instance
    (one routing surface, no divergence). The resolved decomposition
    provider + model are returned so the ``llm-judged`` routing policy
    reuses the very same binding the coordinator decomposes with.

    Returns:
        A ``(coordinator, scorer, decomposition_provider, decomposition_model)``
        tuple sharing the boot engine and a single ``AgentTaskScorer``.

    Raises:
        CoordinationConfigError: If the resolved
            ``coordination.decomposition_model`` reference carries no model
            id while a provider is configured (the coordinator builds
            eagerly and its decomposition strategy requires a non-blank
            model).
    """
    deps = await _resolve_coordinator_dependencies(app_state)
    raw_decomposition_ref = deps.decomposition_ref
    decomposition_strategy = deps.decomposition_strategy
    agent_session_max_turns = deps.agent_session_max_turns
    agent_session_ceilings = deps.agent_session_ceilings
    routing_scorer_config = deps.routing_scorer_config
    workspace_strategy, workspace_config = deps.workspace
    middleware_enabled = deps.middleware_enabled
    planning = deps.planning_memory
    # ``decomposition_model`` is a MODEL_REF: it must name an explicit
    # ``(provider, model_id)`` pair. It is never auto-bound to a default
    # provider, so a bare / unregistered ref resolves to ``None`` and fails
    # loud below (the single-shot fallback decomposer + the llm-judged routing
    # policy both dispatch on this binding).
    decomp_ref = parse_model_ref(raw_decomposition_ref)
    decomposition_model = decomp_ref.model_id
    decomp_provider = resolve_ref_provider(
        app_state,
        decomp_ref,
        event=API_APP_STARTUP,
        subject="decomposition",
    )
    if not decomposition_model.strip() or decomp_provider is None:
        # Fail soft, not hard: raise a typed error so the runtime builder can
        # boot in the degraded no-coordinator mode (task execution rejected at
        # the seam) instead of crashing the whole reload. Logging happens once,
        # at the builder's catch site; this raise site stays silent so a single
        # failure yields a single WARNING rather than one line per boot hook it
        # would propagate through.
        msg = (
            "coordination.decomposition_model must select an explicit"
            " (provider, model) pair from your provider catalogue: the"
            " coordinator builds eagerly at boot and its fallback decomposer +"
            " routing judge dispatch on that binding, which is never"
            " auto-resolved to a default provider."
        )
        raise CoordinationConfigError(msg)
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
    # The agent-session decomposer records its planning spend against the
    # shared cost tracker under the owner + objective task, so charter
    # planning is attributed rather than silently unmetered.
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    # ``AgentEngineExecutionService`` provisions the per-project workspace
    # lazily on first task; bare construction (no service) keeps the
    # persistence-less dev paths working as before.
    from synthorg.core.agent import AgentIdentity  # noqa: PLC0415
    from synthorg.providers.state import provider_registry_of  # noqa: PLC0415

    def _owner_provider_selector(identity: AgentIdentity) -> CompletionProvider:
        # The owner-run decomposition session dispatches on the owner's own
        # bound provider, never the boot default; an unregistered provider
        # raises and the strategy falls back to the single-shot decomposer.
        # Re-resolve the registry live per call so a provider hot-reload swap is
        # reflected without rebuilding the coordinator (mirrors the red-team
        # runtime's per-call resolve).
        return provider_registry_of(app_state).get(identity.model.provider)

    # Real research data beats guessing, so grant live web search when a
    # provider is configured; fail open (no provider -> no tool), matching the
    # research subsystem's web source. The workspace root is always passed:
    # reading the tree of the project being planned is what makes a recalled
    # claim about that project checkable, and recall alone spans every project
    # the org has run.
    planning_tool_provider = PlanningToolProvider(
        search_provider=search_provider,
        memory_backend=planning.memory_backend,
        org_backend=planning.org_backend,
        workspace_root=agent_workspace_root_of(app_state),
    )

    coordinator = build_coordinator(
        config=app_state.config.coordination,
        engine=engine,
        task_assignment_config=app_state.config.task_assignment,
        provider=decomp_provider,
        decomposition_model=decomposition_model,
        provider_selector=_owner_provider_selector,
        decomposition_strategy=decomposition_strategy,
        decomposition_tool_provider=planning_tool_provider,
        decomposition_cost_tracker=cost_tracker,
        # Composed here, where every part of it was just resolved, so the
        # strategy receives one config instead of three scalars a later
        # wiring path could carry partially.
        agent_session_config=AgentSessionDecompositionConfig(
            max_turns=agent_session_max_turns,
            ceilings=agent_session_ceilings,
            memory_digest_budget=planning.digest_budget,
            # The same deployment config the work loop's detector is built
            # from. Read twice rather than shared, because the two loops run
            # concurrently and a detector carries per-loop state.
            stagnation=app_state.config.stagnation,
        ),
        decomposition_config_resolver=config_resolver_of(app_state),
        planning_memory=planning.planning_memory,
        task_engine=task_engine_of(app_state),
        workspace_strategy=workspace_strategy,
        workspace_config=workspace_config,
        project_workspace_service=project_workspace_service,
        git_backend=git_backend,
        performance_tracker=performance_tracker,
        routing=CoordinatorRoutingDeps(
            scorer=scorer,
            scorer_config=routing_scorer_config,
            capability=await build_capability_policy(app_state),
        ),
        coordination_metrics_collector=coordination_metrics_collector,
        coordination_chain=_build_coordination_chain(
            enabled=middleware_enabled,
        ),
        shutdown_manager=app_state.shutdown_manager,
    )
    # Handed over here rather than through the factory, which is already at
    # its approved argument count. Before anything can decompose, so the
    # wall-clock ceiling is the operator's from the first plan rather than
    # from the first restart after they set it.
    coordinator.decomposition_service.set_config_resolver(config_resolver_of(app_state))
    logger.info(
        API_APP_STARTUP,
        service="coordinator",
        mode="multi_agent",
        decomposition_model=decomposition_model,
        topology=app_state.config.coordination.topology.value,
    )
    return coordinator, scorer, decomp_provider, decomposition_model


async def _build_runtime_work_pipeline(
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
    # The routing policy and leaf threshold are independent settings reads;
    # resolve them concurrently so the spine build issues one round-trip
    # window rather than two serial ones.
    resolver = config_resolver_of(app_state)
    async with asyncio.TaskGroup() as tg:
        routing_task = tg.create_task(
            resolver.get_str(_DECOMPOSITION_NS, _ROUTING_POLICY_KEY)
        )
        leaf_task = tg.create_task(
            resolver.get_int(_DECOMPOSITION_NS, _LEAF_THRESHOLD_KEY)
        )
    routing_policy = routing_task.result()
    leaf_threshold = leaf_task.result()
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    # Built here rather than inside the factory because it needs the live
    # capability registry, and memoised on the engine slice so the solo path,
    # the coordination router and dispatch all judge against ONE instance and
    # cannot disagree about what rung an agent runs at.
    capability = await build_capability_policy(app_state)
    assignment_service = build_solo_assignment_service(
        app_state.config.task_assignment.strategy,
        scorer=scorer,
        capability=capability,
    )
    # An agent whose bound pair cannot serve is out, so it is absent from the
    # pool the spine staffs from rather than filtered later by whichever
    # consumer remembers to. With no health tracker wired there is nothing
    # measuring serviceability, so the roster stands unfiltered: an
    # installation that measures nothing has no grounds to call an agent
    # unavailable, and losing the work spine over it would be far worse.
    tracker = app_state.slice(ProvidersStateSlice).health_tracker
    roster = ServiceabilityFilteredRoster(
        agent_registry_of(app_state),
        availability=(
            ServiceabilityAvailabilityReader(
                tracker,
                config_resolver=config_resolver_of(app_state),
            )
            if tracker is not None
            else None
        ),
    )
    return build_work_pipeline(
        intake_engine=intake_engine,
        task_engine=task_engine_of(app_state),
        project_repository=persistence_of(app_state).projects,
        scorer=scorer,
        worker_execution_service=worker_execution_service,
        coordinator=coordinator,
        roster=roster,
        # Built here rather than inside the factory: the threshold is re-read
        # per routing decision, so the policy needs the live resolver, which
        # this assembly holds and the factory does not.
        routing_policy=build_work_routing_policy(
            routing_policy,
            threshold=leaf_threshold,
            provider=provider,
            model=decomposition_model,
            cost_tracker=cost_tracker,
            config_resolver=resolver,
        ),
        assignment_service=assignment_service,
        clock=app_state.clock,
    )
