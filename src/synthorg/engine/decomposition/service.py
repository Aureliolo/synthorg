"""Decomposition service.

Orchestrates strategy, classifier, DAG validation, and task creation
to decompose a parent task into executable subtasks.
"""

import asyncio

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan_tree import SubtreeStep
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import TaskStatus, TaskStructure
from synthorg.core.types import NotBlankStr
from synthorg.engine.assembly import Assembly, build_assembly
from synthorg.engine.decomposition._artifacts import expected_artifact_from_spec
from synthorg.engine.decomposition._ceilings import (
    DEFAULT_SESSION_CEILING_SECONDS,
    DEFAULT_TREE_CEILING_SECONDS,
    ceiling_seconds,
    timeout_failure,
    tree_session_budget,
)
from synthorg.engine.decomposition._ids import subtask_uuid as _subtask_uuid
from synthorg.engine.decomposition._recursion import (
    RecursionBudget,
    TreeSessionLedger,
    child_context,
    resolve_decomposition_bounds,
    resolve_recursion_budget,
    stamp_objective_criteria,
)
from synthorg.engine.decomposition._split_decision import (
    SplitOutcome,
    SplitVerdict,
    assembled_subtasks,
    assembled_task,
    decide_split,
)
from synthorg.engine.decomposition.atomicity import (
    PLANNER_DECLINED,
    SESSION_CEILING_BACKSTOP,
    unsplit_reason,
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
from synthorg.engine.errors import (
    DecompositionError,
    DecompositionTimeoutError,
    DecompositionUnsplittableError,
)
from synthorg.engine.stakes import build_stakes_assessor
from synthorg.engine.stakes.protocol import StakesAssessor
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_CHILD_CEILING_ABSORBED,
    DECOMPOSITION_COMPLETED,
    DECOMPOSITION_FAILED,
    DECOMPOSITION_PLANNER_DECLINED,
    DECOMPOSITION_RECURSED,
    DECOMPOSITION_STARTED,
    DECOMPOSITION_SUBTASK_CREATED,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)


def _held_to_size(
    context: DecompositionContext, budget: RecursionBudget
) -> DecompositionContext:
    """Stamp the size signal onto *context* when this is the last level.

    The single owner of "is there anywhere left to split into". While there
    is, an oversized unit is simply decomposed again, which is the measured
    behaviour; at the last level there is nowhere to delegate to, so the
    planner is asked at parse time to spend BREADTH instead, on the same
    correction channel a graph violation takes.

    Args:
        context: The level about to be planned.
        budget: What may be done about an oversized subtask.

    Returns:
        The context, carrying the policy only where it binds.
    """
    if not budget.enabled or budget.has_room(context):
        return context
    return context.model_copy(update={"atomicity": budget.policy})


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
        # Resolved once, at the root, and stamped onto the context every level
        # below plans under: one tree is never planned under two budgets, and
        # a caller that declared its own shape (the manual endpoints) keeps it.
        context = stamp_objective_criteria(
            task, await resolve_decomposition_bounds(context, self._config_resolver)
        )
        # The outer of the two ceilings, and the only one that bounds a
        # CALLER. The inner one below bounds a planning session, and a
        # recursion runs one per node, so the number of sessions is the
        # branching factor to the power of the depth and no per-session
        # budget bounds the call at all. Two of the four callers are
        # request handlers.
        # The root's own planning session is claimed here, so the budget an
        # operator sets is a count of sessions rather than of recursions.
        ledger = TreeSessionLedger(
            remaining=await tree_session_budget(self._config_resolver)
        )
        ledger.take()
        scope = asyncio.timeout(await self._tree_timeout_seconds())
        try:
            async with scope:
                return await self._do_decompose(
                    task, await self._grounded(task, context), budget, ledger=ledger
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
        self, task: Task, exc: TimeoutError, *, expired: bool, ceiling: str
    ) -> DecompositionError:
        """Classify a ``TimeoutError`` against the scope that caught it.

        Args:
            task: The task being decomposed, for the log line.
            exc: What was caught.
            expired: Whether the ceiling's own scope is what fired.
            ceiling: Which ceiling this site guards, for the log line.

        Returns:
            What to raise.
        """
        return timeout_failure(
            exc,
            task_id=str(task.id),
            strategy=self._strategy.get_strategy_name(),
            expired=expired,
            ceiling=ceiling,
        )

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
        """Read the per-session ceiling in force for this decomposition.

        Read per call rather than captured at construction, so an operator
        raising it for a slow provider applies to the next decomposition
        instead of the next restart.

        Returns:
            The ceiling, in seconds.
        """
        return await ceiling_seconds(
            self._config_resolver,
            "decomposition_timeout_seconds",
            DEFAULT_SESSION_CEILING_SECONDS,
        )

    async def _tree_timeout_seconds(self) -> float:
        """Read the whole-tree ceiling in force for this decomposition.

        Returns:
            The ceiling, in seconds.
        """
        return await ceiling_seconds(
            self._config_resolver,
            "decomposition_tree_timeout_seconds",
            DEFAULT_TREE_CEILING_SECONDS,
        )

    async def _do_decompose(
        self,
        task: Task,
        context: DecompositionContext,
        budget: RecursionBudget,
        *,
        ledger: TreeSessionLedger,
    ) -> DecompositionResult:
        """Internal decomposition logic, and the recursion point.

        Args:
            task: The parent task to decompose.
            context: Decomposition constraints.
            budget: What may be done about an oversized subtask.
            ledger: The whole tree's remaining planning-session budget,
                spent by this level and by every level it opens.

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
                plan = await self._strategy.decompose(
                    task, _held_to_size(context, budget)
                )
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
        split = await self._split_oversized(
            plan.subtasks, created_tasks, context, budget, ledger=ledger
        )
        # 6b. A unit that split is no longer work: it is the assembly of what
        # was split out of it. Applied to the definition AND the task, because
        # routing reads the first and dispatch judges the second, and a unit
        # left as work on either runs its own children's work over again.
        if split.assemblies:
            plan = plan.model_copy(
                update={"subtasks": assembled_subtasks(plan.subtasks, split)}
            )
            created_tasks = [
                assembled_task(built, split.assemblies.get(subtask_def.id))
                for subtask_def, built in zip(plan.subtasks, created_tasks, strict=True)
            ]
        if split.unsplit:
            # Stamped after the tasks exist, because the note belongs to the
            # PLAN an operator reviews rather than to the work: a unit that
            # reached the plan still oversized is one the planner was asked to
            # widen and could not, and nothing else on this path says so.
            plan = plan.model_copy(
                update={
                    "subtasks": tuple(
                        st.model_copy(update={"unsplit_reason": reason})
                        if (reason := split.unsplit.get(st.id))
                        else st
                        for st in plan.subtasks
                    )
                }
            )

        result = DecompositionResult(
            plan=plan,
            created_tasks=tuple(created_tasks),
            dependency_edges=tuple(edges),
            depth=context.current_depth,
            children=split.children,
        )

        logger.info(
            DECOMPOSITION_COMPLETED,
            task_id=str(task.id),
            subtask_count=len(created_tasks),
            structure=structure.value,
            edge_count=len(edges),
            depth=context.current_depth,
            split_count=len(split.children),
            unsplit_count=len(split.unsplit),
            leaf_count=len(result.leaf_tasks),
        )

        return result

    async def _split_oversized(
        self,
        subtasks: tuple[SubtaskDefinition, ...],
        created_tasks: list[Task],
        context: DecompositionContext,
        budget: RecursionBudget,
        *,
        ledger: TreeSessionLedger,
    ) -> SplitOutcome:
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
            ledger: The whole tree's remaining planning-session budget.

        Returns:
            The child results, and the reason each unit that stayed oversized
            went unsplit.
        """
        if not budget.enabled:
            # Nothing here can act on an oversized subtask, so assessing them
            # only to report that recursion is off names a condition that did
            # not occur. The shipped default leaves recursion off and thresholds
            # at one artifact, so every subtask declaring two would take the
            # depth-exhausted branch below, at warning level, on every
            # decomposition the product runs.
            return SplitOutcome(children=(), unsplit={}, assemblies={})

        if not self._strategy.plans_any_task():
            # A strategy holding one operator-supplied plan for one parent
            # cannot plan the child, and says so by raising. Recursing anyway
            # turns an oversized subtask into a failed REQUEST: the manual
            # decomposition endpoint works today and would start refusing every
            # plan whose subtask declares two artifacts the moment an operator
            # enabled recursion, which is a setting about depth breaking a
            # feature about neither.
            return SplitOutcome(children=(), unsplit={}, assemblies={})

        children: list[DecompositionResult] = []
        unsplit: dict[str, str] = {}
        assemblies: dict[str, Assembly] = {}
        for position, (subtask_def, child_task) in enumerate(
            zip(subtasks, created_tasks, strict=True)
        ):
            decision = decide_split(
                subtask_def,
                task_id=str(child_task.id),
                context=context,
                budget=budget,
                ledger=ledger,
            )
            if decision.verdict is SplitVerdict.LEAVE:
                continue
            if decision.reason is not None:
                unsplit[subtask_def.id] = decision.reason
                continue
            step = SubtreeStep(title=str(subtask_def.title), position=position)
            child_ctx = child_context(
                context, step=step, satisfied=subtask_def.satisfies
            )
            try:
                child = await self._do_decompose(
                    child_task, child_ctx, budget, ledger=ledger
                )
            except DecompositionUnsplittableError as exc:
                # The first of the two child failures this level can answer.
                # Its own plan is valid and its other units are dispatchable,
                # so the unit the planner could not divide goes out carrying
                # the reason rather than taking the whole tree above it down
                # with it. Every other decomposition failure still propagates:
                # a transport that kept mangling replies is not something an
                # operator fixes by reading a note on one item.
                unsplit[subtask_def.id] = unsplit_reason(
                    budget.policy.assess(subtask_def), backstop=PLANNER_DECLINED
                )
                logger.warning(
                    DECOMPOSITION_PLANNER_DECLINED,
                    task_id=str(child_task.id),
                    subtask_id=subtask_def.id,
                    current_depth=context.current_depth,
                    error=safe_error_description(exc),
                )
                continue
            except DecompositionTimeoutError as exc:
                # The second, and the same shape reached by a different route.
                # The per-session ceiling bounds ONE node so a level waiting on
                # a provider that never answers cannot hold the tree; letting
                # it propagate hands every node an independent chance to
                # destroy every other node's work instead, which is the
                # opposite of what it is for. A live run reached
                # `sessions_remaining=2` of forty after thirty-nine sessions
                # and discarded the lot because one of them ran 599.7 seconds
                # against a six-hundred-second ceiling.
                #
                # Absorbing it is safe here and only here: this level holds a
                # valid plan to carry the unit, so the outcome is the same one
                # the graceful session budget produces. At the root there is no
                # plan above to carry anything, no handler, and the breach
                # still fails the decomposition. The two remaining bounds are
                # untouched, so a tree cannot buy unbounded time this way: the
                # session budget still caps how many ceilings can be paid, and
                # the whole-tree ceiling still fires as a bare TimeoutError
                # that no handler here catches.
                unsplit[subtask_def.id] = unsplit_reason(
                    budget.policy.assess(subtask_def),
                    backstop=SESSION_CEILING_BACKSTOP,
                )
                logger.warning(
                    DECOMPOSITION_CHILD_CEILING_ABSORBED,
                    task_id=str(child_task.id),
                    subtask_id=subtask_def.id,
                    current_depth=context.current_depth,
                    error=safe_error_description(exc),
                )
                continue
            children.append(child)
            assemblies[subtask_def.id] = build_assembly(
                title=str(subtask_def.title),
                pieces=[str(piece.title) for piece in child.plan.subtasks],
                criteria=[str(c) for c in subtask_def.acceptance_criteria],
                assembled=[piece.stakes for piece in child.plan.subtasks],
                address=(*context.address, step),
            )
            logger.info(
                DECOMPOSITION_RECURSED,
                task_id=str(child_task.id),
                depth=child.depth,
                subtask_count=len(child.created_tasks),
                leaf_count=len(child.leaf_tasks),
                sessions_remaining=ledger.remaining,
                # What the level below is answerable for, and what it claimed
                # to get there. A subtree narrowing to zero is the shape that
                # ends coverage checking for everything under it, and it is
                # otherwise only visible by reading the plan afterwards.
                claimed_count=len(subtask_def.satisfies),
                covers_count=len(child_ctx.objective_criteria),
            )
        return SplitOutcome(
            children=tuple(children), unsplit=unsplit, assemblies=assemblies
        )

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
