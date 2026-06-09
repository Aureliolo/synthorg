"""Work pipeline factory.

``build_work_pipeline`` is the boot construction site (called from
:mod:`synthorg.workers.runtime_builder` behind the provider-present
switch) and the symbol the ghost-wiring manifest enforces.
"""

from typing import TYPE_CHECKING

from synthorg.budget.tracker import CostTracker
from synthorg.core.clock import Clock
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.pipeline.policy import build_work_routing_policy
from synthorg.engine.pipeline.service import DefaultWorkPipeline
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.providers.protocol import CompletionProvider

if TYPE_CHECKING:
    from synthorg.workers.execution_service import WorkerExecutionService

logger = get_logger(__name__)


def build_work_pipeline(  # noqa: PLR0913 -- keyword-only dependency injection
    *,
    intake_engine: IntakeEngine,
    task_engine: TaskEngine,
    project_repository: ProjectRepository,
    scorer: AgentTaskScorer,
    worker_execution_service: WorkerExecutionService,
    coordinator: MultiAgentCoordinator | None,
    agent_registry: AgentRegistryService,
    routing_discriminator: str,
    leaf_threshold: int,
    provider: CompletionProvider | None = None,
    decomposition_model: str | None = None,
    cost_tracker: CostTracker | None = None,
    clock: Clock | None = None,
) -> DefaultWorkPipeline:
    """Construct the fully-wired default work pipeline.

    Args:
        intake_engine: Intake orchestrator.
        task_engine: Single-writer task state owner.
        project_repository: Project-context read seam.
        scorer: Shared agent-task scorer (also used by the
            coordinator) for the solo-path single-agent pick.
        worker_execution_service: Solo-path executor.
        coordinator: Team-path coordinator, or ``None`` (empty
            company).
        agent_registry: Active-agent pool source.
        routing_discriminator: ``coordination.routing_policy`` value.
        leaf_threshold: ``coordination.leaf_subtask_threshold`` value.
        provider: Completion provider (required for ``llm-judged``).
        decomposition_model: Model id for the ``llm-judged`` policy.
        cost_tracker: Optional cost tracker for ``llm-judged``.
        clock: Injectable time source.

    Returns:
        A ready :class:`DefaultWorkPipeline`.
    """
    routing_policy = build_work_routing_policy(
        routing_discriminator,
        threshold=leaf_threshold,
        provider=provider,
        model=decomposition_model,
        cost_tracker=cost_tracker,
    )
    logger.info(
        API_APP_STARTUP,
        service="work_pipeline",
        routing_policy=routing_discriminator,
        leaf_threshold=leaf_threshold,
        coordinator="present" if coordinator is not None else "absent",
    )
    return DefaultWorkPipeline(
        intake_engine=intake_engine,
        task_engine=task_engine,
        project_repository=project_repository,
        routing_policy=routing_policy,
        scorer=scorer,
        worker_execution_service=worker_execution_service,
        coordinator=coordinator,
        agent_registry=agent_registry,
        clock=clock,
    )
