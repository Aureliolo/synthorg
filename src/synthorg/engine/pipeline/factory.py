"""Work pipeline factory.

``build_work_pipeline`` is the boot construction site (called from
:mod:`synthorg.workers.runtime_builder` behind the provider-present
switch) and the symbol the ghost-wiring manifest enforces.
"""

from typing import TYPE_CHECKING

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.clock import Clock
from synthorg.engine.assignment.registry import build_strategy_map
from synthorg.engine.assignment.service import TaskAssignmentService
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
    assignment_strategy: str,
    provider: CompletionProvider | None = None,
    decomposition_model: str | None = None,
    cost_tracker: CostTrackerProtocol | None = None,
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
        assignment_strategy: ``task_assignment.strategy`` name; selects
            the solo-path ``TaskAssignmentService`` strategy. An unknown
            name degrades to the direct-scorer path with a warning.
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
    # Build the solo-path assignment service from the configured
    # strategy, reusing the shared scorer so the strategy ranks
    # candidates identically to the legacy direct-scorer path while
    # adding the service's status validation + project-team filter. An
    # unknown / hierarchy-only strategy name (``hierarchical`` needs a
    # resolver this factory does not own) degrades to the direct-scorer
    # path rather than failing the pipeline build.
    strategy = build_strategy_map(scorer=scorer).get(assignment_strategy)
    if strategy is None:
        logger.warning(
            API_APP_STARTUP,
            service="work_pipeline",
            note="unknown assignment strategy; using direct-scorer solo path",
            assignment_strategy=assignment_strategy,
        )
        assignment_service = None
    else:
        assignment_service = TaskAssignmentService(strategy)
    logger.info(
        API_APP_STARTUP,
        service="work_pipeline",
        routing_policy=routing_discriminator,
        leaf_threshold=leaf_threshold,
        assignment_strategy=assignment_strategy,
        assignment_service="present" if assignment_service is not None else "absent",
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
        assignment_service=assignment_service,
    )
