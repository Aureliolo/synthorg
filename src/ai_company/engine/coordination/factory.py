"""Coordinator factory — builds a fully wired MultiAgentCoordinator.

Constructs the decomposition, routing, execution, and workspace
dependency tree from config and runtime services.
"""

from typing import TYPE_CHECKING

from ai_company.engine.coordination.service import MultiAgentCoordinator
from ai_company.engine.decomposition.classifier import TaskStructureClassifier
from ai_company.engine.decomposition.protocol import DecompositionStrategy
from ai_company.engine.decomposition.service import DecompositionService
from ai_company.engine.errors import DecompositionError
from ai_company.engine.parallel import ParallelExecutor
from ai_company.engine.routing.scorer import AgentTaskScorer
from ai_company.engine.routing.service import TaskRoutingService
from ai_company.engine.routing.topology_selector import TopologySelector
from ai_company.observability import get_logger
from ai_company.observability.events.coordination import (
    COORDINATION_FACTORY_BUILT,
)
from ai_company.observability.events.decomposition import (
    DECOMPOSITION_FAILED,
)

if TYPE_CHECKING:
    from ai_company.config.schema import TaskAssignmentConfig
    from ai_company.core.task import Task
    from ai_company.engine.agent_engine import AgentEngine
    from ai_company.engine.coordination.section_config import (
        CoordinationSectionConfig,
    )
    from ai_company.engine.decomposition.models import (
        DecompositionContext,
        DecompositionPlan,
    )
    from ai_company.engine.shutdown import ShutdownManager
    from ai_company.engine.task_engine import TaskEngine
    from ai_company.engine.workspace.config import WorkspaceIsolationConfig
    from ai_company.engine.workspace.protocol import WorkspaceIsolationStrategy
    from ai_company.engine.workspace.service import WorkspaceIsolationService
    from ai_company.providers.protocol import CompletionProvider

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
        """Raise DecompositionError — no provider configured."""
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
            is supplied — both or neither must be given.
    """
    if provider is not None and decomposition_model is not None:
        from ai_company.engine.decomposition.llm import (  # noqa: PLC0415
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
) -> WorkspaceIsolationService | None:
    """Build workspace isolation service if both deps are provided.

    Raises:
        ValueError: If exactly one of *workspace_strategy* /
            *workspace_config* is supplied — both or neither must be given.
    """
    if workspace_strategy is not None and workspace_config is not None:
        from ai_company.engine.workspace.service import (  # noqa: PLC0415
            WorkspaceIsolationService,
        )

        return WorkspaceIsolationService(
            strategy=workspace_strategy,
            config=workspace_config,
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
    shutdown_manager: ShutdownManager | None = None,
) -> MultiAgentCoordinator:
    """Build a fully wired :class:`MultiAgentCoordinator`.

    Constructs the dependency tree:
        1. ``TaskStructureClassifier`` (no deps)
        2. ``DecompositionStrategy`` — LLM if provider+model provided,
           otherwise a placeholder that raises at decompose-time
        3. ``DecompositionService(strategy, classifier)``
        4. ``AgentTaskScorer(min_score=task_assignment_config.min_score)``
        5. ``TopologySelector(config.auto_topology_rules)``
        6. ``TaskRoutingService(scorer, topology_selector)``
        7. ``ParallelExecutor(engine=engine)``
        8. ``WorkspaceIsolationService`` if workspace deps provided
        9. ``MultiAgentCoordinator(decomposition, routing, executor, ...)``

    Args:
        config: Company-level coordination section config.
        engine: Agent execution engine (for parallel executor).
        task_assignment_config: Task assignment config (for min_score).
        provider: Optional LLM provider for decomposition.
        decomposition_model: Optional model ID for decomposition.
        task_engine: Optional task engine for parent status updates.
        workspace_strategy: Optional workspace isolation strategy.
        workspace_config: Optional workspace isolation config.
        shutdown_manager: Optional shutdown manager for the executor.

    Returns:
        A fully constructed ``MultiAgentCoordinator``.
    """
    classifier = TaskStructureClassifier()
    strategy = _build_decomposition_strategy(provider, decomposition_model)
    decomposition_service = DecompositionService(strategy, classifier)

    scorer = AgentTaskScorer(min_score=task_assignment_config.min_score)
    topology_selector = TopologySelector(config.auto_topology_rules)
    routing_service = TaskRoutingService(scorer, topology_selector)

    parallel_executor = ParallelExecutor(
        engine=engine,
        shutdown_manager=shutdown_manager,
    )

    coordinator = MultiAgentCoordinator(
        decomposition_service=decomposition_service,
        routing_service=routing_service,
        parallel_executor=parallel_executor,
        workspace_service=_build_workspace_service(
            workspace_strategy, workspace_config
        ),
        task_engine=task_engine,
    )

    logger.debug(
        COORDINATION_FACTORY_BUILT,
        topology=config.topology.value,
        has_provider=provider is not None,
        has_workspace=workspace_strategy is not None,
    )

    return coordinator
