"""Decomposition service.

Orchestrates strategy, classifier, DAG validation, and task creation
to decompose a parent task into executable subtasks.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import TaskStatus
from synthorg.core.task import Task
from synthorg.engine.decomposition.dag import DependencyGraph
from synthorg.engine.decomposition.models import (
    DecompositionResult,
    SubtaskStatusRollup,
)
from synthorg.engine.decomposition.rollup import StatusRollup
from synthorg.engine.errors import DecompositionError
from synthorg.engine.stakes import build_stakes_assessor
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_COMPLETED,
    DECOMPOSITION_FAILED,
    DECOMPOSITION_STARTED,
    DECOMPOSITION_SUBTASK_CREATED,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.decomposition.classifier import TaskStructureClassifier
    from synthorg.engine.decomposition.models import DecompositionContext
    from synthorg.engine.decomposition.protocol import DecompositionStrategy
    from synthorg.engine.stakes.protocol import StakesAssessor

logger = get_logger(__name__)


def _subtask_uuid(subtask_id: str) -> UUID:
    """Parse a subtask id into the UUID used for the child task.

    The LLM strategy emits throwaway labels and remaps them to UUID
    strings before the plan reaches this service, so its ids always
    parse. A hand-built plan from a custom ``DecompositionStrategy`` is
    responsible for supplying UUID-string subtask ids; surface a clear
    domain error if it does not, rather than letting an opaque
    ``ValueError`` escape from deep in task construction.

    Args:
        subtask_id: The plan subtask id to convert.

    Returns:
        The id as a ``UUID``.

    Raises:
        DecompositionError: When ``subtask_id`` is not a canonical UUID
            string.
    """
    try:
        parsed = UUID(subtask_id)
    except ValueError as exc:
        msg = (
            f"Subtask id {subtask_id!r} is not a valid UUID string; "
            "decomposition strategies must supply UUID-string subtask ids"
        )
        raise DecompositionError(msg) from exc
    # The plan keeps the original string while the child Task canonicalises
    # via UUID; a non-canonical input (uppercase, no hyphens) would yield two
    # textual ids for one subtask and break string-based correlation.
    canonical = str(parsed)
    if subtask_id != canonical:
        msg = (
            f"Subtask id {subtask_id!r} is not in canonical UUID form; "
            f"use {canonical!r}"
        )
        raise DecompositionError(msg)
    return parsed


class DecompositionService:
    """Service orchestrating task decomposition.

    Composes a decomposition strategy with a structure classifier,
    DAG validator, and task factory to produce executable subtasks.
    """

    __slots__ = ("_classifier", "_stakes_assessor", "_strategy")

    def __init__(
        self,
        strategy: DecompositionStrategy,
        classifier: TaskStructureClassifier,
        stakes_assessor: StakesAssessor | None = None,
    ) -> None:
        self._strategy = strategy
        self._classifier = classifier
        self._stakes_assessor = stakes_assessor or build_stakes_assessor()

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
            task_id=str(task.id),
            strategy=self._strategy.get_strategy_name(),
            current_depth=context.current_depth,
        )

        try:
            return await self._do_decompose(task, context)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                DECOMPOSITION_FAILED,
                task_id=str(task.id),
                strategy=self._strategy.get_strategy_name(),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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

        # 3b. Assess per-subtask stakes and stamp it onto both the plan
        # subtasks and the tasks created from them, so the plan and the
        # executable tasks agree on stakes for the routing layer.
        assessed_subtasks = tuple(
            st.model_copy(
                update={"stakes": self._stakes_assessor.assess_subtask(st)},
            )
            for st in plan.subtasks
        )
        plan = plan.model_copy(update={"subtasks": assessed_subtasks})

        # 4. Create Task objects
        created_tasks: list[Task] = []
        for subtask_def in plan.subtasks:
            child_task = Task(
                id=_subtask_uuid(subtask_def.id),
                title=subtask_def.title,
                description=subtask_def.description,
                type=task.type,
                priority=task.priority,
                project=task.project,
                created_by=task.created_by,
                parent_task_id=str(task.id),
                delegation_chain=task.delegation_chain,
                dependencies=subtask_def.dependencies,
                status=TaskStatus.CREATED,
                estimated_complexity=subtask_def.estimated_complexity,
                stakes=subtask_def.stakes,
            )
            created_tasks.append(child_task)
            logger.debug(
                DECOMPOSITION_SUBTASK_CREATED,
                parent_task_id=str(task.id),
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
            task_id=str(task.id),
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
