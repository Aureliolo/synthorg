"""Work pipeline factory.

``build_work_pipeline`` is the boot construction site (called from
:mod:`synthorg.workers.runtime_builder` behind the provider-present
switch) and the symbol the ghost-wiring manifest enforces.
"""

from typing import TYPE_CHECKING

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.clock import Clock
from synthorg.engine.assignment._shared import (
    STRATEGY_NAME_HIERARCHICAL,
    STRATEGY_NAME_ROLE_BASED,
)
from synthorg.engine.assignment.registry import build_strategy_map
from synthorg.engine.assignment.service import TaskAssignmentService
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.pipeline.errors import WorkPipelineConfigError
from synthorg.engine.pipeline.policy import build_work_routing_policy
from synthorg.engine.pipeline.service import DefaultWorkPipeline
from synthorg.engine.roster import AvailableRoster
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.engine.routing_policy.capability_policy import CapabilityPolicy
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.providers.protocol import CompletionProvider

if TYPE_CHECKING:
    from synthorg.workers.execution_service import WorkerExecutionService

logger = get_logger(__name__)


def build_solo_assignment_service(
    assignment_strategy: str,
    *,
    scorer: AgentTaskScorer,
    capability: CapabilityPolicy | None = None,
) -> TaskAssignmentService | None:
    """Build the solo-path assignment service for a configured strategy.

    Reuses the shared scorer so the strategy ranks candidates identically to
    the direct-scorer fallback, while adding the service's status validation
    and the capability ladder.

    Args:
        assignment_strategy: ``task_assignment.strategy`` value.
        scorer: The shared agent-task scorer.
        capability: The org's one capability policy.

    Returns:
        The wired service. ``hierarchical`` needs a ``HierarchyResolver`` no
        boot path here owns, so it degrades to the same scorer without the
        hierarchy pool filter, still wrapped in the service: the missing
        collaborator costs the hierarchy ordering, and must not also drop
        the capability ladder, which is an org rule rather than a property of
        one strategy.

    Raises:
        WorkPipelineConfigError: If ``assignment_strategy`` is an unknown
            strategy name (boot misconfiguration).
    """
    strategies = build_strategy_map(
        scorer=scorer,
        capability=capability,
    )
    strategy = strategies.get(assignment_strategy)
    if strategy is not None:
        return TaskAssignmentService(strategy, capability=capability)
    if assignment_strategy == STRATEGY_NAME_HIERARCHICAL:
        logger.warning(
            API_APP_STARTUP,
            service="work_pipeline",
            note=(
                "hierarchical strategy needs a resolver; using role-based"
                " scoring, capability ladder unchanged"
            ),
            assignment_strategy=assignment_strategy,
        )
        return TaskAssignmentService(
            strategies[STRATEGY_NAME_ROLE_BASED],
            capability=capability,
        )
    # An unknown name is a misconfiguration: fail the build loudly at boot
    # rather than silently running a degraded solo path for the lifetime of
    # the process.
    logger.error(
        API_APP_STARTUP,
        service="work_pipeline",
        note="unknown assignment strategy",
        assignment_strategy=assignment_strategy,
    )
    msg = f"unknown task_assignment.strategy '{assignment_strategy}'"
    raise WorkPipelineConfigError(msg)


def build_work_pipeline(  # noqa: PLR0913 -- keyword-only dependency injection
    *,
    intake_engine: IntakeEngine,
    task_engine: TaskEngine,
    project_repository: ProjectRepository,
    scorer: AgentTaskScorer,
    worker_execution_service: WorkerExecutionService,
    coordinator: MultiAgentCoordinator | None,
    roster: AvailableRoster,
    routing_discriminator: str,
    leaf_threshold: int,
    assignment_service: TaskAssignmentService | None,
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
        roster: Staffable-agent pool source: the active agents whose bound
            model can currently serve work.
        routing_discriminator: ``coordination.routing_policy`` value.
        leaf_threshold: ``coordination.leaf_subtask_threshold`` value.
        assignment_service: Solo-path assignment service, from
            :func:`build_solo_assignment_service`. It is built by the caller
            because it carries the stakes capability floor, which needs the
            live capability registry this factory does not reach.
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
        roster=roster,
        clock=clock,
        assignment_service=assignment_service,
    )
