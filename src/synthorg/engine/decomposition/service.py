"""Decomposition service.

Orchestrates strategy, classifier, DAG validation, and task creation
to decompose a parent task into executable subtasks.
"""

import asyncio
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import TaskStatus, TaskStructure
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._artifacts import expected_artifact_from_spec
from synthorg.engine.decomposition._ids import subtask_uuid as _subtask_uuid
from synthorg.engine.decomposition._recursion import (
    RecursionBudget,
    child_context,
    resolve_recursion_budget,
)
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.dag import DependencyGraph
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.plan_context import with_plan_context
from synthorg.engine.decomposition.protocol import (
    DecompositionStrategy,
    WorkspaceInventory,
)
from synthorg.engine.decomposition.rollup import StatusRollup
from synthorg.engine.decomposition.status_rollup import SubtaskStatusRollup
from synthorg.engine.errors import DecompositionError, DecompositionTimeoutError
from synthorg.engine.stakes import build_stakes_assessor
from synthorg.engine.stakes.protocol import StakesAssessor
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_CEILING_UNREADABLE,
    DECOMPOSITION_COMPLETED,
    DECOMPOSITION_DEPTH_EXHAUSTED,
    DECOMPOSITION_FAILED,
    DECOMPOSITION_RECURSED,
    DECOMPOSITION_STARTED,
    DECOMPOSITION_SUBTASK_CREATED,
    DECOMPOSITION_SUBTASK_OVERSIZED,
)
from synthorg.settings.errors import SettingNotFoundError
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: Mirrors ``coordination.decomposition_timeout_seconds``. Held here because a
#: harness runs with no settings at all, and the bound has to stand there too.
_DEFAULT_DECOMPOSITION_TIMEOUT_SECONDS: Final[float] = 600.0

#: Mirrors ``coordination.decomposition_tree_timeout_seconds``, for the same
#: reason.
_DEFAULT_TREE_TIMEOUT_SECONDS: Final[float] = 3600.0


def _task_from_subtask(
    parent: Task,
    plan: DecompositionPlan,
    subtask_def: SubtaskDefinition,
) -> Task:
    """Build the executable task one subtask definition describes.

    Args:
        parent: The task being decomposed.
        plan: The plan the definition came from, for its plan-level facts.
        subtask_def: The definition to realise.

    Returns:
        The child :class:`Task`.
    """
    return Task(
        id=_subtask_uuid(subtask_def.id),
        title=subtask_def.title,
        description=NotBlankStr(
            with_plan_context(
                subtask_def.description,
                assumptions=plan.assumptions,
                open_questions=plan.open_questions,
            )
        ),
        type=parent.type,
        priority=parent.priority,
        project=parent.project,
        created_by=parent.created_by,
        parent_task_id=str(parent.id),
        delegation_chain=parent.delegation_chain,
        dependencies=subtask_def.dependencies,
        acceptance_criteria=tuple(
            AcceptanceCriterion(description=c) for c in subtask_def.acceptance_criteria
        ),
        artifacts_expected=tuple(
            expected_artifact_from_spec(a) for a in subtask_def.expected_artifacts
        ),
        status=TaskStatus.CREATED,
        estimated_complexity=subtask_def.estimated_complexity,
        stakes=subtask_def.stakes,
    )


class DecompositionService:
    """Service orchestrating task decomposition.

    Composes a decomposition strategy with a structure classifier,
    DAG validator, and task factory to produce executable subtasks.
    """

    __slots__ = (
        "_classifier",
        "_config_resolver",
        "_stakes_assessor",
        "_strategy",
        "_workspace_inventory",
    )

    def __init__(
        self,
        strategy: DecompositionStrategy,
        classifier: TaskStructureClassifier,
        stakes_assessor: StakesAssessor | None = None,
        *,
        config_resolver: ConfigResolverProtocol | None = None,
        workspace_inventory: WorkspaceInventory | None = None,
    ) -> None:
        self._strategy = strategy
        self._classifier = classifier
        self._stakes_assessor = stakes_assessor or build_stakes_assessor()
        self._config_resolver = config_resolver
        self._workspace_inventory = workspace_inventory

    async def _grounded(
        self, task: Task, context: DecompositionContext
    ) -> DecompositionContext:
        """Return *context* carrying what the project's workspace holds.

        Resolved here, at the one seam every decomposition path comes through,
        rather than at each construction site: two of the five build their
        context deep inside the engine with no access to a workspace root, and
        the charter route is one of them, so a per-caller answer leaves the
        main intake path ungrounded.

        A caller that already knows the inventory keeps its own answer, which
        is what lets a harness plan against a workspace no disk holds.

        Returns:
            The context, with ``workspace_summary`` filled when an inventory is
            wired and the caller did not already supply one.
        """
        if self._workspace_inventory is None or context.workspace_summary is not None:
            return context
        summary = await self._workspace_inventory.describe_inventory(task.project)
        return context.model_copy(update={"workspace_summary": summary})

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
            DecompositionTimeoutError: When any one planning session outruns
                ``coordination.decomposition_timeout_seconds``, or the whole
                tree outruns ``coordination.decomposition_tree_timeout_seconds``.
                Its own type because neither ceiling moves on a retry.
            DecompositionError: When something inside timed out on its own
                without either ceiling firing, which IS worth retrying, and for
                every other decomposition failure.
        """
        logger.info(
            DECOMPOSITION_STARTED,
            task_id=str(task.id),
            strategy=self._strategy.get_strategy_name(),
            current_depth=context.current_depth,
        )

        budget = await resolve_recursion_budget(self._config_resolver)
        # The outer of the two ceilings, and the only one that bounds a
        # CALLER. The inner one below bounds a planning session, and a
        # recursion runs one per node, so the number of sessions is the
        # branching factor to the power of the depth and no per-session
        # budget bounds the call at all. Two of the four callers are
        # request handlers.
        scope = asyncio.timeout(await self._tree_timeout_seconds())
        try:
            async with scope:
                return await self._do_decompose(
                    task, await self._grounded(task, context), budget
                )
        except TimeoutError as exc:
            # Asked of the scope, not inferred from the type: this handler also
            # sees a TimeoutError that something INSIDE raised without any
            # ceiling firing, and the two deserve opposite answers. A ceiling
            # is unchanged on the next attempt, so a retry pays it again to
            # reach the same place; a call that timed out on its own is the
            # ordinary transient a retry exists for.
            raise self._timeout_failure(
                task, exc, expired=scope.expired(), ceiling="whole-tree"
            ) from exc
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

    def _timeout_failure(
        self,
        task: Task,
        exc: TimeoutError,
        *,
        expired: bool,
        ceiling: str,
    ) -> DecompositionError:
        """Classify a ``TimeoutError`` and log it, returning what to raise.

        Args:
            task: The task being decomposed, for the log line.
            exc: What was caught.
            expired: Whether the ceiling's own scope is what fired.
            ceiling: Which ceiling this site guards, for the log line.

        Returns:
            The error to raise: the non-retryable type when the ceiling fired,
            the ordinary one when something inside timed out on its own.
        """
        msg = (
            f"Decomposition outran its {ceiling} wall-clock ceiling"
            if expired
            else "A call inside the decomposition timed out"
        )
        logger.warning(
            DECOMPOSITION_FAILED,
            task_id=str(task.id),
            strategy=self._strategy.get_strategy_name(),
            error_type=type(exc).__name__,
            error=msg,
        )
        return DecompositionTimeoutError(msg) if expired else DecompositionError(msg)

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

    async def _ceiling_seconds(self, key: str, default: float) -> float:
        """Read the wall-clock ceiling *key* in force for this decomposition.

        Read per call rather than captured at construction, so an operator
        raising a ceiling for a slow provider applies to the next decomposition
        instead of the next restart.

        Only the two failures the resolver documents fall back: the key is not
        registered, or its stored value is not a float. Both are facts about
        the setting, unchanged until someone changes it, and the default is the
        honest answer to either. Anything else, a dead settings store above
        all, propagates: it is transient, the ceiling is re-read per node of a
        recursion, and swallowing it silently substitutes a bound nobody chose
        for as long as the store stays down. A sweep arming a ceiling in the
        tens of thousands of seconds and quietly getting the default back is
        exactly the failure the arming exists to prevent.

        Args:
            key: The coordination setting naming the ceiling.
            default: The definition's own default, in force when there is no
                resolver (a harness) or the setting cannot answer.

        Returns:
            The ceiling, in seconds.
        """
        resolver = self._config_resolver
        if resolver is None:
            return default
        try:
            return await resolver.get_float("coordination", key)
        except (SettingNotFoundError, ValueError) as exc:
            # lint-allow: swallow-ok -- a ceiling the setting cannot answer for
            # is the definition's default by construction, and a bound still
            # stands, so the unbounded wait this exists to prevent cannot happen
            logger.warning(
                DECOMPOSITION_CEILING_UNREADABLE,
                setting=key,
                fallback_seconds=default,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return default

    async def _timeout_seconds(self) -> float:
        """Read the per-session ceiling in force for this decomposition.

        Returns:
            The ceiling, in seconds.
        """
        return await self._ceiling_seconds(
            "decomposition_timeout_seconds", _DEFAULT_DECOMPOSITION_TIMEOUT_SECONDS
        )

    async def _tree_timeout_seconds(self) -> float:
        """Read the whole-tree ceiling in force for this decomposition.

        Returns:
            The ceiling, in seconds.
        """
        return await self._ceiling_seconds(
            "decomposition_tree_timeout_seconds", _DEFAULT_TREE_TIMEOUT_SECONDS
        )

    async def _do_decompose(
        self,
        task: Task,
        context: DecompositionContext,
        budget: RecursionBudget,
    ) -> DecompositionResult:
        """Internal decomposition logic, and the recursion point.

        Args:
            task: The parent task to decompose.
            context: Decomposition constraints.
            budget: What may be done about an oversized subtask.

        Returns:
            Decomposition result with created tasks, dependency edges, and the
            decomposition of each subtask that was split further.

        Raises:
            DecompositionTimeoutError: The per-session ceiling fired, which the
                next attempt would reach identically.
            TimeoutError: Something inside timed out on its own, left as it was
                raised so the caller's handler classifies it against its own
                scope rather than inheriting this one's verdict.
        """
        # 1. Decompose via strategy.
        #
        # The inner of the two ceilings: one PLANNING SESSION, so a level
        # waiting on a provider that never answers cannot hold the tree, and
        # every level is bounded rather than sharing one budget with its
        # siblings. Here rather than per caller, so the answer cannot differ by
        # entry point. It is deliberately NOT derived from depth, which is why
        # the whole-tree bound in `decompose_task` is a separate setting rather
        # than a multiple of this one: sessions scale with the NODE COUNT, the
        # branching factor to the power of the depth, so any multiple is a
        # guess that kills a legitimate deep tree and discards every level it
        # had already paid for.
        scope = asyncio.timeout(await self._timeout_seconds())
        try:
            async with scope:
                plan = await self._strategy.decompose(task, context)
        except TimeoutError as exc:
            # Classified here rather than left to the caller's handler, which
            # can only ask its OWN scope: this ceiling firing is as unretryable
            # as the tree one, and to that handler it is indistinguishable from
            # a call that timed out on its own.
            if scope.expired():
                raise self._timeout_failure(
                    task, exc, expired=True, ceiling="planning-session"
                ) from exc
            raise

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
            child_task = _task_from_subtask(task, plan, subtask_def)
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

        # 6. Split whatever is more than one agent's worth of work. Done after
        # the tasks exist because a child level decomposes the TASK, not the
        # definition: it inherits the project, the type and the delegation
        # chain the same way any other dispatch would.
        children = await self._split_oversized(
            plan.subtasks, created_tasks, context, budget
        )

        result = DecompositionResult(
            plan=plan,
            created_tasks=tuple(created_tasks),
            dependency_edges=tuple(edges),
            depth=context.current_depth,
            children=children,
        )

        logger.info(
            DECOMPOSITION_COMPLETED,
            task_id=str(task.id),
            subtask_count=len(created_tasks),
            structure=structure.value,
            edge_count=len(edges),
            depth=context.current_depth,
            split_count=len(children),
            leaf_count=len(result.leaf_tasks),
        )

        return result

    async def _split_oversized(
        self,
        subtasks: tuple[SubtaskDefinition, ...],
        created_tasks: list[Task],
        context: DecompositionContext,
        budget: RecursionBudget,
    ) -> tuple[DecompositionResult, ...]:
        """Decompose again every subtask that is more than one agent's worth.

        Sequential rather than fanned out: each child level is itself a
        planning session against a provider, and a level of eight subtasks
        fanning out concurrently at every level turns one decomposition into a
        burst nothing here rate-limits.

        Args:
            subtasks: This level's definitions, aligned with *created_tasks*.
            created_tasks: The tasks built from them.
            context: This level's constraints.
            budget: What may be done about an oversized subtask.

        Returns:
            One result per subtask that split, empty when none did.
        """
        if not budget.enabled:
            # Nothing here can act on an oversized subtask, so assessing them
            # only to report that recursion is off names a condition that did
            # not occur. The shipped default leaves recursion off and thresholds
            # at one artifact, so every subtask declaring two would take the
            # depth-exhausted branch below, at warning level, on every
            # decomposition the product runs.
            return ()

        if not self._strategy.plans_any_task():
            # A strategy holding one operator-supplied plan for one parent
            # cannot plan the child, and says so by raising. Recursing anyway
            # turns an oversized subtask into a failed REQUEST: the manual
            # decomposition endpoint works today and would start refusing every
            # plan whose subtask declares two artifacts the moment an operator
            # enabled recursion, which is a setting about depth breaking a
            # feature about neither.
            return ()

        children: list[DecompositionResult] = []
        for subtask_def, child_task in zip(subtasks, created_tasks, strict=True):
            if subtask_def.kind is not PlanItemKind.WORK:
                # A DECISION item is a choice among its declared options, not
                # work to divide, and the policy reads only the artifact,
                # criterion and claim counts. One declaring several acceptance
                # criteria would otherwise read as oversized and open a child
                # planning session that plans work nobody asked for, which the
                # harness then tries to build as a leaf.
                continue
            assessment = budget.policy.assess(subtask_def)
            if not assessment.is_oversized:
                continue
            if not budget.has_room(context):
                logger.warning(
                    DECOMPOSITION_DEPTH_EXHAUSTED,
                    task_id=str(child_task.id),
                    subtask_id=subtask_def.id,
                    condition=assessment.condition,
                    observed=assessment.observed,
                    limit=assessment.limit,
                    current_depth=context.current_depth,
                    max_depth=context.max_depth,
                )
                continue
            logger.info(
                DECOMPOSITION_SUBTASK_OVERSIZED,
                task_id=str(child_task.id),
                subtask_id=subtask_def.id,
                condition=assessment.condition,
                observed=assessment.observed,
                limit=assessment.limit,
                current_depth=context.current_depth,
            )
            child = await self._do_decompose(child_task, child_context(context), budget)
            children.append(child)
            logger.info(
                DECOMPOSITION_RECURSED,
                task_id=str(child_task.id),
                depth=child.depth,
                subtask_count=len(child.created_tasks),
                leaf_count=len(child.leaf_tasks),
            )
        return tuple(children)

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
