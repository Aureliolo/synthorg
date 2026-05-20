"""Coordinator factory -- builds a fully wired MultiAgentCoordinator.

Constructs the decomposition, routing, execution, and workspace
dependency tree from config and runtime services.
"""

from typing import TYPE_CHECKING

from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.errors import DecompositionError
from synthorg.engine.parallel import ParallelExecutor
from synthorg.engine.routing.scorer import AgentTaskScorer, RoutingScorerConfig
from synthorg.engine.routing.service import TaskRoutingService
from synthorg.engine.routing.topology_selector import TopologySelector
from synthorg.observability import get_logger
from synthorg.observability.events.coordination import (
    COORDINATION_FACTORY_BUILT,
)
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_FAILED,
)

if TYPE_CHECKING:
    from synthorg.budget.coordination_collector import (
        CoordinationMetricsCollector,
    )
    from synthorg.config.schema import TaskAssignmentConfig
    from synthorg.core.enums import CoordinationTopology
    from synthorg.core.task import Task
    from synthorg.engine.agent_engine import AgentEngine
    from synthorg.engine.coordination.section_config import (
        CoordinationSectionConfig,
    )
    from synthorg.engine.decomposition.models import (
        DecompositionContext,
        DecompositionPlan,
    )
    from synthorg.engine.shutdown import ShutdownManager
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.engine.workspace.config import WorkspaceIsolationConfig
    from synthorg.engine.workspace.git_backend import GitBackend
    from synthorg.engine.workspace.protocol import WorkspaceIsolationStrategy
    from synthorg.engine.workspace.service import WorkspaceIsolationService
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)


class _NoProviderDecompositionStrategy(DecompositionStrategy):
    """Placeholder strategy that raises when no LLM provider is available.

    Used when the factory is called without a provider, so that the
    coordinator can still be constructed (e.g. for manual decomposition
    tests). Attempting to actually decompose will raise a clear error.
    """

    def get_strategy_name(self) -> str:
        """Return placeholder strategy name."""
        return "no-provider-placeholder"

    async def decompose(
        self,
        task: Task,  # noqa: ARG002
        context: DecompositionContext,  # noqa: ARG002
    ) -> DecompositionPlan:
        """Raise DecompositionError -- no provider configured."""
        msg = (
            "No LLM provider configured for decomposition. "
            "Provide a CompletionProvider and decomposition_model "
            "to enable LLM-based task decomposition."
        )
        logger.warning(
            DECOMPOSITION_FAILED,
            note="Decomposition attempted without LLM provider",
        )
        raise DecompositionError(msg)


def _build_decomposition_strategy(
    provider: CompletionProvider | None,
    decomposition_model: str | None,
) -> DecompositionStrategy:
    """Select the decomposition strategy based on available deps.

    Raises:
        ValueError: If exactly one of *provider* / *decomposition_model*
            is supplied -- both or neither must be given.
    """
    if provider is not None and decomposition_model is not None:
        from synthorg.engine.decomposition.llm import (  # noqa: PLC0415
            LlmDecompositionStrategy,
        )

        return LlmDecompositionStrategy(
            provider=provider,
            model=decomposition_model,
        )
    if (provider is None) != (decomposition_model is None):
        given = "provider" if provider is not None else "decomposition_model"
        missing = "decomposition_model" if provider is not None else "provider"
        msg = (
            f"Decomposition requires both provider and decomposition_model, "
            f"but only {given} was supplied (missing {missing})"
        )
        logger.warning(
            DECOMPOSITION_FAILED,
            note="Mismatched decomposition dependencies",
            given=given,
            missing=missing,
        )
        raise ValueError(msg)
    return _NoProviderDecompositionStrategy()


def _build_workspace_service(
    workspace_strategy: WorkspaceIsolationStrategy | None,
    workspace_config: WorkspaceIsolationConfig | None,
    git_backend: GitBackend | None = None,
) -> WorkspaceIsolationService | None:
    """Build workspace isolation service if both deps are provided.

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
    task_engine: TaskEngine | None = None,
    workspace_strategy: WorkspaceIsolationStrategy | None = None,
    workspace_config: WorkspaceIsolationConfig | None = None,
    git_backend: GitBackend | None = None,
    shutdown_manager: ShutdownManager | None = None,
    performance_tracker: PerformanceTracker | None = None,
    routing_scorer_config: RoutingScorerConfig | None = None,
    coordination_metrics_collector: CoordinationMetricsCollector | None = None,
    scorer: AgentTaskScorer | None = None,
) -> MultiAgentCoordinator:
    """Build a fully wired :class:`MultiAgentCoordinator`.

    Constructs the dependency tree:
        1. ``TaskStructureClassifier`` (no deps)
        2. ``DecompositionStrategy`` -- LLM if provider+model provided,
           otherwise a placeholder that raises at decompose-time
        3. ``DecompositionService(strategy, classifier)``
        4. ``AgentTaskScorer`` -- instantiated with
           *routing_scorer_config* (operator-tunable weights resolved
           from ``EngineBridgeConfig`` via
           :meth:`RoutingScorerConfig.from_bridge_config`) if provided,
           else with the legacy ``min_score`` override only
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
        task_engine: Optional task engine for parent status updates.
        workspace_strategy: Optional workspace isolation strategy.
        workspace_config: Optional workspace isolation config.
        git_backend: Optional pluggable git backend; when provided, the
            workspace service routes per-project merge+push through the
            serial :class:`PushQueueCoordinator` for forge-collision
            safety. ``None`` keeps the legacy in-process merge path.
        shutdown_manager: Optional shutdown manager for the executor.
        performance_tracker: Optional tracker for recording
            per-agent coordination contributions.
        routing_scorer_config: Operator-tunable scorer weights. Pass
            ``RoutingScorerConfig.from_bridge_config(bridge)`` after
            resolving an ``EngineBridgeConfig`` at startup so changes
            via ``/settings`` flow into the routing scorer. ``None``
            falls back to scorer defaults that mirror the historical
            hardcoded values; ``task_assignment_config.min_score`` is
            still honoured as a min-score override in that case.
        coordination_metrics_collector: Shared collector the coordinator
            invokes post-completion to compute and record the
            multi-agent metrics. ``None`` disables collection (the
            ``/coordination/metrics`` API stays empty).
        scorer: Pre-built agent-task scorer to share with the work
            pipeline's solo-path selection so both routing surfaces
            use one instance. ``None`` builds one from
            *routing_scorer_config* / *task_assignment_config* as
            before.

    Returns:
        A fully constructed ``MultiAgentCoordinator``.
    """
    classifier = TaskStructureClassifier()
    strategy = _build_decomposition_strategy(provider, decomposition_model)
    decomposition_service = DecompositionService(strategy, classifier)

    if scorer is None:
        if routing_scorer_config is None:
            scorer = AgentTaskScorer(min_score=task_assignment_config.min_score)
        else:
            scorer = AgentTaskScorer(config=routing_scorer_config)
    topology_selector = TopologySelector(config.auto_topology_rules)
    routing_service = TaskRoutingService(scorer, topology_selector)

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
        task_engine=task_engine,
        performance_tracker=performance_tracker,
        default_topology_provider=_topology_provider,
        coordination_metrics_collector=coordination_metrics_collector,
    )

    logger.debug(
        COORDINATION_FACTORY_BUILT,
        topology=config.topology.value,
        has_provider=provider is not None,
        has_workspace=workspace_strategy is not None,
    )

    return coordinator
