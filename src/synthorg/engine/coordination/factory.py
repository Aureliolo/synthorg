"""Coordinator factory -- builds a fully wired MultiAgentCoordinator.

Constructs the decomposition, routing, execution, and workspace
dependency tree from config and runtime services.
"""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from synthorg.core.task_enums import CoordinationTopology
from synthorg.engine.coordination.decomposition_strategy_factory import (
    build_decomposition_strategy,
)
from synthorg.engine.coordination.section_config import (
    CoordinationSectionConfig,
)
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.decomposition.strategy_deps import DecompositionStrategyDeps
from synthorg.engine.parallel import ParallelExecutor
from synthorg.engine.routing.scorer import AgentTaskScorer, RoutingScorerConfig
from synthorg.engine.routing.service import TaskRoutingService
from synthorg.engine.routing.topology_selector import TopologySelector
from synthorg.engine.routing_policy.capability_policy import CapabilityPolicy
from synthorg.engine.workspace.config import WorkspaceIsolationConfig
from synthorg.engine.workspace.git_backend import GitBackend
from synthorg.engine.workspace.protocol import WorkspaceIsolationStrategy
from synthorg.observability import get_logger
from synthorg.observability.events.coordination import (
    COORDINATION_FACTORY_BUILT,
)
from synthorg.providers.protocol import CompletionProvider

if TYPE_CHECKING:
    # config.schema would cycle here (it pulls api -> engine); the concrete
    # services below are faked in tests, so a runtime import would make
    # typeguard enforce a nominal isinstance the fakes cannot satisfy.
    from synthorg.budget.coordination_collector import (
        CoordinationMetricsCollector,
    )
    from synthorg.config.agent_schema import TaskAssignmentConfig
    from synthorg.engine.agent_engine import AgentEngine
    from synthorg.engine.middleware.coordination_protocol import (
        CoordinationMiddlewareChain,
    )
    from synthorg.engine.shutdown import ShutdownManager
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.engine.workspace.project_workspace_service import (
        ProjectWorkspaceService,
    )
    from synthorg.engine.workspace.service import WorkspaceIsolationService
    from synthorg.hr.performance.tracker import PerformanceTracker

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CoordinatorRoutingDeps:
    """Everything that decides which agent a subtask goes to.

    Bundled because the three answer one question together and are wired
    from one place: the capability policy narrows the pool to the band that
    fits the work, and the scorer ranks within it. Passing them separately
    spread one decision across three arguments of a factory that already
    takes more than anything should.

    Attributes:
        scorer: Pre-built agent-task scorer, shared with the work pipeline's
            solo-path selection so both routing surfaces use one instance.
            ``None`` builds one from *scorer_config*, falling back to the
            ``task_assignment_config.min_score`` override.
        scorer_config: Operator-tunable scorer weights. Pass
            ``RoutingScorerConfig.from_bridge_config(bridge)`` after resolving
            an ``EngineBridgeConfig`` at startup so ``/settings`` changes flow
            into the routing scorer. Ignored when *scorer* is supplied.
        capability: The org's one capability policy, shared with the solo path
            and with dispatch so a subtask is never routed to an agent the
            dispatch will then refuse. ``None`` routes on score alone.
    """

    scorer: AgentTaskScorer | None = None
    scorer_config: RoutingScorerConfig | None = None
    capability: CapabilityPolicy | None = None


def _build_scorer(
    scorer_config: RoutingScorerConfig | None,
    task_assignment_config: TaskAssignmentConfig,
) -> AgentTaskScorer:
    """Build the routing scorer when the caller supplied none.

    Returns:
        A scorer on the operator's tuned weights, or on the assignment
        config's ``min_score`` override when no weights were resolved.
    """
    if scorer_config is None:
        return AgentTaskScorer(min_score=task_assignment_config.min_score)
    return AgentTaskScorer(config=scorer_config)


def _build_workspace_service(
    workspace_strategy: WorkspaceIsolationStrategy | None,
    workspace_config: WorkspaceIsolationConfig | None,
    git_backend: GitBackend | None = None,
) -> WorkspaceIsolationService | None:
    """Build workspace isolation service if both deps are provided.

    Returns:
        A :class:`WorkspaceIsolationService` when both
        ``workspace_strategy`` and ``workspace_config`` are wired;
        ``None`` when both are absent (and ``git_backend`` is also
        unset).

    Raises:
        ValueError: If exactly one of *workspace_strategy* /
            *workspace_config* is supplied -- both or neither must be
            given. Also raised when *git_backend* is supplied without
            *workspace_strategy* AND *workspace_config*: routing pushes
            through the coordinator-owned push queue is only possible
            when a workspace service exists, so accepting the backend
            silently disables it (the queueing feature ships dead).
    """
    if workspace_strategy is not None and workspace_config is not None:
        from synthorg.engine.workspace.service import (  # noqa: PLC0415
            WorkspaceIsolationService,
        )

        return WorkspaceIsolationService(
            strategy=workspace_strategy,
            config=workspace_config,
            git_backend=git_backend,
        )
    if (workspace_strategy is None) != (workspace_config is None):
        given = (
            "workspace_strategy"
            if workspace_strategy is not None
            else "workspace_config"
        )
        missing = (
            "workspace_config"
            if workspace_strategy is not None
            else "workspace_strategy"
        )
        msg = (
            f"Workspace isolation requires both workspace_strategy and "
            f"workspace_config, but only {given} was supplied (missing {missing})"
        )
        logger.warning(
            COORDINATION_FACTORY_BUILT,
            note="Mismatched workspace dependencies",
            given=given,
            missing=missing,
        )
        raise ValueError(msg)
    if git_backend is not None:
        # Neither workspace dep is set, but a git backend was supplied:
        # the push-queue routing feature can only fire through a
        # workspace service, so silently accepting the backend would
        # ship the queueing path disabled while pretending it works.
        msg = (
            "git_backend was supplied without workspace_strategy and "
            "workspace_config; routing pushes through the coordinator "
            "requires a workspace service. Provide both workspace deps "
            "or omit git_backend."
        )
        logger.warning(
            COORDINATION_FACTORY_BUILT,
            note="git_backend without workspace deps",
        )
        raise ValueError(msg)
    return None


def build_coordinator(  # noqa: PLR0913
    *,
    config: CoordinationSectionConfig,
    engine: AgentEngine,
    task_assignment_config: TaskAssignmentConfig,
    provider: CompletionProvider | None = None,
    decomposition_model: str | None = None,
    decomposition_strategy: str = "agent-session",
    decomposition: DecompositionStrategyDeps | None = None,
    task_engine: TaskEngine | None = None,
    workspace_strategy: WorkspaceIsolationStrategy | None = None,
    workspace_config: WorkspaceIsolationConfig | None = None,
    project_workspace_service: ProjectWorkspaceService | None = None,
    git_backend: GitBackend | None = None,
    shutdown_manager: ShutdownManager | None = None,
    performance_tracker: PerformanceTracker | None = None,
    routing: CoordinatorRoutingDeps | None = None,
    coordination_metrics_collector: CoordinationMetricsCollector | None = None,
    coordination_chain: CoordinationMiddlewareChain | None = None,
) -> MultiAgentCoordinator:
    """Build a fully wired :class:`MultiAgentCoordinator`.

    Constructs the dependency tree:
        1. ``TaskStructureClassifier`` (no deps)
        2. ``DecompositionStrategy`` -- selected by *decomposition_strategy*
           (``agent-session`` default, or ``llm``) when provider+model are
           provided; otherwise a placeholder that raises at decompose-time
        3. ``DecompositionService(strategy, classifier,
           workspace_inventory=project_workspace_service)`` -- the inventory
           is what tells the planner which files a project actually has, so
           ``None`` leaves the plan to be written against org-wide recall
           instead
        4. ``AgentTaskScorer`` -- instantiated with
           *routing_scorer_config* (operator-tunable weights resolved
           from ``EngineBridgeConfig`` via
           :meth:`RoutingScorerConfig.from_bridge_config`) if provided,
           else with the ``min_score`` scalar override only
        5. ``TopologySelector(config.auto_topology_rules)``
        6. ``TaskRoutingService(scorer, topology_selector)``
        7. ``ParallelExecutor(engine=engine)``
        8. ``WorkspaceIsolationService`` if workspace deps provided
        9. ``MultiAgentCoordinator(decomposition, routing, executor, ...)``

    Args:
        config: Company-level coordination section config.
        engine: Agent execution engine (for parallel executor).
        task_assignment_config: Task assignment config (for min_score
            fallback when ``routing_scorer_config`` is not provided).
        provider: Optional LLM provider for decomposition.
        decomposition_model: Optional model ID for decomposition.
        decomposition_strategy: Which decomposer to build -- ``"agent-session"``
            (default; owner-run planning loop) or ``"llm"`` (single-shot). Read
            from ``coordination.decomposition_strategy`` at boot.
        decomposition: Everything the chosen strategy is wired with beyond its
            model: the owner's provider selector, the planning tool provider,
            the cost tracker, the session config, the memory digest, the
            settings resolver and the live agent-state repository. See
            :class:`DecompositionStrategyDeps`. ``None`` builds the strategy on
            its own defaults, which the ``agent-session`` path refuses because
            it has no provider selector to dispatch each owner on.
        task_engine: Optional task engine for parent status updates.
        workspace_strategy: Optional workspace isolation strategy.
        workspace_config: Optional workspace isolation config.
        project_workspace_service: Optional per-project workspace
            provisioner. Threaded to the coordinator so the dispatch
            merge step resolves each project's repo root and routes
            through the per-project push queue.
        git_backend: Optional pluggable git backend; when provided, the
            workspace service routes per-project merge+push through the
            serial :class:`PushQueueCoordinator` for forge-collision
            safety. ``None`` keeps the in-process merge path.
        shutdown_manager: Optional shutdown manager for the executor.
        performance_tracker: Optional tracker for recording
            per-agent coordination contributions.
        routing: How a subtask's agent is chosen: the capability policy that
            narrows the pool and the scorer that ranks within it. See
            :class:`CoordinatorRoutingDeps`. ``None`` builds a scorer from
            ``task_assignment_config.min_score`` and routes on score alone.
        coordination_metrics_collector: Shared collector the coordinator
            invokes post-completion to compute and record the
            multi-agent metrics. ``None`` disables collection (the
            ``/coordination/metrics`` API stays empty).
        coordination_chain: Optional coordination middleware pipeline to
            run around the coordinate() phases. ``None`` (the default)
            disables middleware entirely, preserving current behaviour.

    Returns:
        A fully constructed ``MultiAgentCoordinator``.
    """
    classifier = TaskStructureClassifier()
    decomposition = decomposition or DecompositionStrategyDeps()
    # The agent-session planner halts at a turn boundary when a graceful
    # shutdown begins; the coordinator already holds the manager for the
    # executor, so the loop's checker is derived here rather than asked of the
    # caller, and it replaces whatever the deps arrived carrying.
    strategy = build_decomposition_strategy(
        provider,
        decomposition_model,
        strategy_name=decomposition_strategy,
        deps=replace(
            decomposition,
            shutdown_checker=(
                shutdown_manager.is_shutting_down
                if shutdown_manager is not None
                else None
            ),
        ),
    )
    # The workspace service doubles as the planner's inventory: it is the only
    # thing here that knows where a project's files actually are, and a plan
    # written without that fact is written against whatever org-wide recall
    # happens to surface, which spans every project the org has ever run.
    decomposition_service = DecompositionService(
        strategy,
        classifier,
        workspace_inventory=project_workspace_service,
        progress_reporter=decomposition.progress_reporter,
        clock=decomposition.clock,
    )

    routing = routing or CoordinatorRoutingDeps()
    scorer = routing.scorer or _build_scorer(
        routing.scorer_config, task_assignment_config
    )
    topology_selector = TopologySelector(config.auto_topology_rules)
    routing_service = TaskRoutingService(
        scorer, topology_selector, capability=routing.capability
    )

    parallel_executor = ParallelExecutor(
        engine=engine,
        shutdown_manager=shutdown_manager,
    )

    # Capture the config object itself (not ``config.topology`` by
    # value) so that when settings subscribers mutate the coordination
    # config at runtime, the coordinator's topology fallback picks up
    # the new value without requiring a coordinator rebuild.
    def _topology_provider() -> CoordinationTopology:
        return config.topology

    coordinator = MultiAgentCoordinator(
        decomposition_service=decomposition_service,
        routing_service=routing_service,
        parallel_executor=parallel_executor,
        workspace_service=_build_workspace_service(
            workspace_strategy, workspace_config, git_backend
        ),
        project_workspace_service=project_workspace_service,
        task_engine=task_engine,
        performance_tracker=performance_tracker,
        default_topology_provider=_topology_provider,
        coordination_metrics_collector=coordination_metrics_collector,
        coordination_chain=coordination_chain,
    )

    logger.debug(
        COORDINATION_FACTORY_BUILT,
        topology=config.topology.value,
        has_provider=provider is not None,
        has_workspace=workspace_strategy is not None,
    )

    return coordinator
