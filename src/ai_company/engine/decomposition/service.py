"""Decomposition service.

Orchestrates strategy, classifier, DAG validation, and task creation
to decompose a parent task into executable subtasks.
"""

from typing import TYPE_CHECKING

from ai_company.core.enums import TaskStatus
from ai_company.core.task import Task
from ai_company.engine.decomposition.dag import DependencyGraph
from ai_company.engine.decomposition.models import (
    DecompositionResult,
    SubtaskStatusRollup,
)
from ai_company.engine.decomposition.rollup import StatusRollup
from ai_company.observability import get_logger
from ai_company.observability.events.decomposition import (
    DECOMPOSITION_COMPLETED,
    DECOMPOSITION_FAILED,
    DECOMPOSITION_STARTED,
    DECOMPOSITION_SUBTASK_CREATED,
)

if TYPE_CHECKING:
    from ai_company.core.types import NotBlankStr
    from ai_company.engine.decomposition.classifier import TaskStructureClassifier
    from ai_company.engine.decomposition.models import DecompositionContext
    from ai_company.engine.decomposition.protocol import DecompositionStrategy

logger = get_logger(__name__)


class DecompositionService:
    """Service orchestrating task decomposition.

    Composes a decomposition strategy with a structure classifier,
    DAG validator, and task factory to produce executable subtasks.
    """

    __slots__ = ("_classifier", "_strategy")

    def __init__(
        self,
        strategy: DecompositionStrategy,
        classifier: TaskStructureClassifier,
    ) -> None:
        self._strategy = strategy
        self._classifier = classifier

    async def decompose_task(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionResult:
        """Decompose a task into subtasks.

        1. Classify task structure (uses explicit if set,
           otherwise heuristic inference). Override the plan's
           structure with the classifier's result when they differ.
        2. Call strategy.decompose().
        3. Validate DAG via DependencyGraph.
        4. Create Task objects from SubtaskDefinitions.
        5. Return DecompositionResult.

        Args:
            task: The parent task to decompose.
            context: Decomposition constraints.

        Returns:
            Decomposition result with created tasks and dependency edges.
        """
        logger.info(
            DECOMPOSITION_STARTED,
            task_id=task.id,
            strategy=self._strategy.get_strategy_name(),
            current_depth=context.current_depth,
        )

        try:
            return await self._do_decompose(task, context)
        except Exception:
            logger.exception(
                DECOMPOSITION_FAILED,
                task_id=task.id,
                strategy=self._strategy.get_strategy_name(),
            )
            raise

    async def _do_decompose(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionResult:
        """Internal decomposition logic.

        Args:
            task: The parent task to decompose.
            context: Decomposition constraints.

        Returns:
            Decomposition result with created tasks and dependency edges.
        """
        # 1. Classify structure
        structure = self._classifier.classify(task)

        # 2. Decompose via strategy
        plan = await self._strategy.decompose(task, context)

        # Override structure if classifier found something different
        if plan.task_structure != structure:
            plan = plan.model_copy(update={"task_structure": structure})

        # 3. Validate DAG
        graph = DependencyGraph(plan.subtasks)
        graph.validate()

        # 4. Create Task objects
        created_tasks: list[Task] = []
        for subtask_def in plan.subtasks:
            child_task = Task(
                id=subtask_def.id,
                title=subtask_def.title,
                description=subtask_def.description,
                type=task.type,
                priority=task.priority,
                project=task.project,
                created_by=task.created_by,
                parent_task_id=task.id,
                delegation_chain=task.delegation_chain,
                dependencies=subtask_def.dependencies,
                status=TaskStatus.CREATED,
                estimated_complexity=subtask_def.estimated_complexity,
            )
            created_tasks.append(child_task)
            logger.debug(
                DECOMPOSITION_SUBTASK_CREATED,
                parent_task_id=task.id,
                subtask_id=subtask_def.id,
                title=subtask_def.title,
            )

        # 5. Build dependency edges
        edges: list[tuple[str, str]] = []
        for subtask_def in plan.subtasks:
            edges.extend(
                (dep_id, subtask_def.id) for dep_id in subtask_def.dependencies
            )

        result = DecompositionResult(
            plan=plan,
            created_tasks=tuple(created_tasks),
            dependency_edges=tuple(edges),
        )

        logger.info(
            DECOMPOSITION_COMPLETED,
            task_id=task.id,
            subtask_count=len(created_tasks),
            structure=plan.task_structure.value,
            edge_count=len(edges),
        )

        return result

    def rollup_status(
        self,
        parent_task_id: NotBlankStr,
        subtask_statuses: tuple[TaskStatus, ...],
    ) -> SubtaskStatusRollup:
        """Compute status rollup for a parent task.

        Args:
            parent_task_id: The parent task identifier.
            subtask_statuses: Statuses of all subtasks.

        Returns:
            Aggregated status rollup.
        """
        return StatusRollup.compute(parent_task_id, subtask_statuses)
