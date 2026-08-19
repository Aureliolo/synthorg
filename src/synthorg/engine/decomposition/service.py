"""Decomposition service.

Orchestrates strategy, classifier, DAG validation, and task creation
to decompose a parent task into executable subtasks.
"""

import asyncio
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import TaskStatus, TaskStructure
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._artifacts import expected_artifact_from_spec
from synthorg.engine.decomposition._ids import subtask_uuid as _subtask_uuid
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.dag import DependencyGraph
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    DecompositionResult,
    SubtaskStatusRollup,
)
from synthorg.engine.decomposition.plan_context import with_plan_context
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.engine.decomposition.rollup import StatusRollup
from synthorg.engine.errors import DecompositionError
from synthorg.engine.stakes import build_stakes_assessor
from synthorg.engine.stakes.protocol import StakesAssessor
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_COMPLETED,
    DECOMPOSITION_FAILED,
    DECOMPOSITION_STARTED,
    DECOMPOSITION_SUBTASK_CREATED,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: Mirrors ``coordination.decomposition_timeout_seconds``. Held here because a
#: harness runs with no settings at all, and the bound has to stand there too.
_DEFAULT_DECOMPOSITION_TIMEOUT_SECONDS: Final[float] = 600.0


class DecompositionService:
    """Service orchestrating task decomposition.

    Composes a decomposition strategy with a structure classifier,
    DAG validator, and task factory to produce executable subtasks.
    """

    __slots__ = ("_classifier", "_config_resolver", "_stakes_assessor", "_strategy")

    def __init__(
        self,
        strategy: DecompositionStrategy,
        classifier: TaskStructureClassifier,
        stakes_assessor: StakesAssessor | None = None,
        *,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        self._strategy = strategy
        self._classifier = classifier
        self._stakes_assessor = stakes_assessor or build_stakes_assessor()
        self._config_resolver = config_resolver

    async def decompose_task(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionResult:
        """Decompose a task into subtasks.

        1. Call strategy.decompose().
        2. Resolve the task structure: the planner's own declaration
           stands; only a plan that declared none falls to the
           classifier.
        3. Validate DAG via DependencyGraph.
        4. Create Task objects from SubtaskDefinitions.
        5. Return DecompositionResult.

        Args:
            task: The parent task to decompose.
            context: Decomposition constraints.

        Returns:
            Decomposition result with created tasks and dependency edges.

        Raises:
            DecompositionError: When the whole operation outruns
                ``coordination.decomposition_timeout_seconds``.
        """
        logger.info(
            DECOMPOSITION_STARTED,
            task_id=str(task.id),
            strategy=self._strategy.get_strategy_name(),
            current_depth=context.current_depth,
        )

        try:
            # Bounded here rather than per caller: a planning session waiting
            # on a provider that never answers holds whatever called it, and
            # two of the four callers are request handlers, so an unbounded
            # call occupies an HTTP worker for as long as the provider stalls.
            # One ceiling at the one place every caller comes through, so the
            # answer cannot differ by entry point.
            async with asyncio.timeout(await self._timeout_seconds()):
                return await self._do_decompose(task, context)
        except TimeoutError as exc:
            msg = "Decomposition outran its wall-clock ceiling"
            logger.warning(
                DECOMPOSITION_FAILED,
                task_id=str(task.id),
                strategy=self._strategy.get_strategy_name(),
                error_type=type(exc).__name__,
                error=msg,
            )
            raise DecompositionError(msg) from exc
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

    def set_config_resolver(self, resolver: ConfigResolverProtocol) -> None:
        """Adopt the resolver the ceiling is read through.

        A setter rather than a constructor argument because the coordinator
        factory that builds this service is already at its approved argument
        count, and threading one more through it would widen a signature the
        repository pins. The resolver is handed over right after the
        coordinator is assembled, before anything can decompose.

        Args:
            resolver: The live settings resolver.
        """
        self._config_resolver = resolver

    async def _timeout_seconds(self) -> float:
        """Read the wall-clock ceiling in force for this decomposition.

        Read per call rather than captured at construction, so an operator
        raising the ceiling for a slow provider applies to the next
        decomposition instead of the next restart.

        Returns:
            The configured ceiling, or the definition's default when there is
            no resolver (a harness) or it cannot answer. Falling back to the
            default keeps a bound in force: the failure this exists to prevent
            is an unbounded wait, and a settings read that failed is no reason
            to grant one.
        """
        resolver = self._config_resolver
        if resolver is None:
            return _DEFAULT_DECOMPOSITION_TIMEOUT_SECONDS
        try:
            return await resolver.get_float(
                "coordination", "decomposition_timeout_seconds"
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                DECOMPOSITION_FAILED,
                note="decomposition timeout unreadable; the default ceiling stands",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return _DEFAULT_DECOMPOSITION_TIMEOUT_SECONDS

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
        # 1. Decompose via strategy
        plan = await self._strategy.decompose(task, context)

        # 2. Resolve the structure. The planner reasoned over the whole
        # objective, so its declaration stands; the keyword heuristic is
        # the fallback for a plan that declared nothing, never an override.
        structure = plan.task_structure
        if structure is TaskStructure.AUTO:
            structure = self._classifier.classify(task)
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

        # 4. Create Task objects. The plan's assumptions and unanswered
        # questions ride on every child description: they are plan-level
        # facts, and this is the only place a plan-level fact reaches the
        # agent that does the work.
        created_tasks: list[Task] = []
        for subtask_def in plan.subtasks:
            child_task = Task(
                id=_subtask_uuid(subtask_def.id),
                title=subtask_def.title,
                description=NotBlankStr(
                    with_plan_context(
                        subtask_def.description,
                        assumptions=plan.assumptions,
                        open_questions=plan.open_questions,
                    )
                ),
                type=task.type,
                priority=task.priority,
                project=task.project,
                created_by=task.created_by,
                parent_task_id=str(task.id),
                delegation_chain=task.delegation_chain,
                dependencies=subtask_def.dependencies,
                acceptance_criteria=tuple(
                    AcceptanceCriterion(description=c)
                    for c in subtask_def.acceptance_criteria
                ),
                artifacts_expected=tuple(
                    expected_artifact_from_spec(a)
                    for a in subtask_def.expected_artifacts
                ),
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
            structure=structure.value,
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
