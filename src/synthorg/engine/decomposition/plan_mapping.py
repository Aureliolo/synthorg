# module-kind: code
"""Project between a transient ``DecompositionResult`` and a durable ``Plan``.

The decomposition layer produces a ``DecompositionResult`` (the executed
subtask tree). To make a plan first-class (reviewable, revisable, and
outliving the approval decision), that transient shape is projected onto the
persisted :class:`~synthorg.core.plan.Plan` entity, and back again at dispatch
time so an operator-edited plan is the one that actually builds. This module
owns both directions so the gate, the API, and the resume path stay in step.
"""

from collections.abc import Iterator, Sequence
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.plan_review import PlanReview
from synthorg.core.plan_tree import PlanTree
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStructure
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._item_tasks import assembly_of, task_from_item
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.errors import DecompositionError
from synthorg.observability import get_logger
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_REBUILT_FROM_PLAN,
    DECOMPOSITION_VALIDATION_ERROR,
)

logger = get_logger(__name__)


class PlanProvenance(BaseModel):
    """The plan-level provenance a durable ``Plan`` carries beyond its items.

    Bundles the objective/project identity, timing, lifecycle, and denormalised
    review context so :func:`plan_from_decomposition` takes one typed argument
    rather than a long provenance parameter list.

    Attributes:
        project: Project the plan belongs to.
        project_name: Human name of that project, denormalised onto the plan
            so no surface has to resolve an id to say which project it means.
        objective_id: Charter/objective the plan serves.
        objective_title: Human title of the objective, denormalised onto the
            plan so the review surface never shows a raw id.
        parent_task_id: The objective task that was decomposed.
        created_at: Timestamp stamped on both ``created_at`` and ``updated_at``
            (a freshly built plan has never been revised).
        status: Initial lifecycle status (defaults to pending review).
        forecast_id: Cost forecast released alongside the plan, if any.
        review: The consolidated stakeholder-panel review, if a panel ran.
        review_absent_reason: Why a seated panel produced no review, so an
            unreviewed plan says so instead of looking merely un-panelled.
        objective_criteria: The objective's acceptance criteria, denormalised
            onto the plan so the coverage map can flag any uncovered criterion.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project: NotBlankStr = Field(description="Project the plan belongs to")
    project_name: NotBlankStr = Field(description="Human name of that project")
    objective_id: NotBlankStr = Field(description="Charter/objective the plan serves")
    objective_title: NotBlankStr = Field(description="Human title of the objective")
    parent_task_id: NotBlankStr = Field(description="Objective task decomposed")
    created_at: AwareDatetime = Field(description="Creation timestamp (tz-aware UTC)")
    status: PlanStatus = Field(
        default=PlanStatus.PENDING_REVIEW,
        description="Initial lifecycle status",
    )
    forecast_id: UUID | None = Field(
        default=None,
        description="Cost forecast released alongside the plan",
    )
    review: PlanReview | None = Field(
        default=None,
        description="The consolidated stakeholder-panel review, if a panel ran",
    )
    review_absent_reason: NotBlankStr | None = Field(
        default=None,
        description="Why a seated panel produced no review",
    )
    objective_criteria: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="The objective's acceptance criteria, denormalised",
    )


def _item_from_subtask(
    subtask: SubtaskDefinition, *, parent_id: NotBlankStr | None
) -> PlanItem:
    """Project one decomposition subtask onto a durable plan item.

    Args:
        subtask: The definition the planner produced.
        parent_id: The item this one was split out of, or ``None`` at the
            root, where an item is one of the plan's workstreams.

    Returns:
        A :class:`PlanItem` carrying the subtask's identity, dependency
        edges, routing complexity/stakes, owning role, and the per-item
        acceptance criteria and expected artifacts that arm the fail-loud
        zero-artifact guard on the dispatched task.
    """
    return PlanItem(
        id=subtask.id,
        title=subtask.title,
        description=subtask.description,
        parent_id=parent_id,
        dependencies=subtask.dependencies,
        owner=subtask.required_role,
        acceptance_criteria=subtask.acceptance_criteria,
        expected_artifacts=subtask.expected_artifacts,
        required_skills=subtask.required_skills,
        required_tags=subtask.required_tags,
        estimated_complexity=subtask.estimated_complexity,
        stakes=subtask.stakes,
        kind=subtask.kind,
        options=subtask.options,
        satisfies=subtask.satisfies,
        unsplit_reason=subtask.unsplit_reason,
    )


def items_from_decomposition(result: DecompositionResult) -> tuple[PlanItem, ...]:
    """Project a decomposition's subtask tree onto durable plan items.

    Split out from :func:`plan_from_decomposition` because a re-plan needs the
    items alone: its successor keeps the retired plan's provenance and is built
    by the plan service, not assembled here.

    Every level reaches the plan. A child node's ``plan.parent_task_id`` IS the
    id of the subtask it was split out of (``_validate_children`` refuses a
    child naming anything else, and a level's task ids are its subtask ids), so
    the parent link is read off the tree rather than derived a second way.

    Returns:
        One :class:`PlanItem` per subtask at every level, workstreams first
        and then each subtree, so the tuple reads top-down.
    """
    return tuple(_walk_levels(result, parent_id=None))


def _walk_levels(
    node: DecompositionResult, *, parent_id: NotBlankStr | None
) -> Iterator[PlanItem]:
    """Yield *node*'s own items, then each split subtask's subtree.

    Yields:
        Each item of the tree rooted at *node*.
    """
    for subtask in node.plan.subtasks:
        yield _item_from_subtask(subtask, parent_id=parent_id)
    for child in node.children:
        yield from _walk_levels(child, parent_id=child.plan.parent_task_id)


def plan_from_decomposition(
    result: DecompositionResult,
    provenance: PlanProvenance,
) -> Plan:
    """Build a durable ``Plan`` from an executed ``DecompositionResult``.

    Args:
        result: The decomposition whose subtask tree becomes the plan items.
        provenance: The plan-level identity, timing, lifecycle, and review
            context to stamp onto the plan (see :class:`PlanProvenance`).

    Returns:
        A validated :class:`Plan` mirroring the decomposition's structure.

    Raises:
        DecompositionError: If the decomposition's structure was never
            resolved, which means it did not come through
            ``DecompositionService``. Substituting a default here would
            sequentialise the plan silently.
    """
    items = items_from_decomposition(result)
    structure = result.plan.task_structure
    if structure is TaskStructure.AUTO:
        msg = "Decomposition reached plan mapping with an unresolved task_structure"
        logger.warning(
            DECOMPOSITION_VALIDATION_ERROR,
            parent_task_id=provenance.parent_task_id,
            error=msg,
        )
        raise DecompositionError(msg)
    return Plan(
        project=provenance.project,
        project_name=provenance.project_name,
        objective_id=provenance.objective_id,
        objective_title=provenance.objective_title,
        parent_task_id=provenance.parent_task_id,
        items=items,
        task_structure=structure,
        coordination_topology=result.plan.coordination_topology,
        status=provenance.status,
        forecast_id=provenance.forecast_id,
        review=provenance.review,
        review_absent_reason=provenance.review_absent_reason,
        objective_criteria=provenance.objective_criteria,
        open_questions=result.plan.open_questions,
        assumptions=result.plan.assumptions,
        planning_strategy=result.plan.planning_strategy,
        created_at=provenance.created_at,
        updated_at=provenance.created_at,
    )


def plan_shell(provenance: PlanProvenance) -> Plan:
    """Build a durable ``Plan`` shell persisted at greenlight, before decomposition.

    The shell carries the objective / project identity but no items yet: the
    decomposer fills them in (via :func:`plan_from_decomposition`, moving the
    plan to ``PENDING_REVIEW``), or a failed decomposition marks it ``FAILED``.
    Persisting it up front makes every greenlit objective leave a first-class,
    visible plan even when decomposition never completes, rather than a silent
    orphan task.

    Args:
        provenance: The plan-level identity and timing to stamp; its ``status``
            is normally :attr:`PlanStatus.PLANNING` for a shell.

    Returns:
        A validated :class:`Plan` with an empty item list (permitted for the
        PLANNING / FAILED statuses).
    """
    return Plan(
        project=provenance.project,
        project_name=provenance.project_name,
        objective_id=provenance.objective_id,
        objective_title=provenance.objective_title,
        parent_task_id=provenance.parent_task_id,
        items=(),
        status=provenance.status,
        forecast_id=provenance.forecast_id,
        objective_criteria=provenance.objective_criteria,
        created_at=provenance.created_at,
        updated_at=provenance.created_at,
    )


def _subtask_from_item(item: PlanItem, *, tree: PlanTree) -> SubtaskDefinition:
    """Project a durable plan item back onto a decomposition subtask.

    The item's ``owner`` maps back to the subtask's ``required_role``, and the
    per-item acceptance criteria and expected artifacts round-trip so a
    re-decomposition off a durable plan keeps the guard armed.

    A container's stakes come from the same :func:`assembly_of` verdict the
    task does, because these two are read at the two ends of one decision:
    routing admits candidates against the definition's value and dispatch
    judges the task's, so a container routed on the un-escalated value is
    placed with an agent the escalated one then refuses.

    Returns:
        A :class:`SubtaskDefinition` mirroring the plan item.
    """
    assembly = assembly_of(item, tree=tree)
    return SubtaskDefinition(
        id=item.id,
        title=item.title,
        description=item.description,
        dependencies=item.dependencies,
        estimated_complexity=item.estimated_complexity,
        stakes=item.stakes if assembly is None else assembly.stakes,
        required_skills=item.required_skills,
        required_tags=item.required_tags,
        required_role=item.owner,
        expected_artifacts=item.expected_artifacts,
        acceptance_criteria=item.acceptance_criteria,
        kind=item.kind,
        options=item.options,
        satisfies=item.satisfies,
        unsplit_reason=item.unsplit_reason,
    )


def decomposition_from_plan(
    plan: Plan,
    *,
    parent_task: Task,
) -> DecompositionResult:
    """Rebuild the dispatchable ``DecompositionResult`` from a durable plan.

    The inverse of :func:`plan_from_decomposition`: it reconstructs the subtask
    tree, child tasks, and dependency edges so an operator-edited plan is the
    one that actually builds on approval (no reliance on the frozen snapshot
    captured at gate time).

    The tree is rebuilt from the persisted parent links rather than flattened,
    so ``leaf_tasks``, ``all_tasks`` and ``max_depth_reached`` say what they
    mean on the result the coordinator is handed, and each child task hangs
    off its own container rather than off the objective.

    Args:
        plan: The durable plan to dispatch (its items become the subtasks).
        parent_task: The objective task the plan decomposes; supplies the
            routing context inherited by every level.

    Returns:
        A validated :class:`DecompositionResult` ready for
        ``coordinate(precomputed_plan=...)``.
    """
    # Decision items are resolved by the reviewer's choice, not executed, so they
    # never dispatch; their ids are stripped from remaining items' dependencies
    # (the decision is already made by approval time), leaving a work-only DAG.
    decision_ids = frozenset(
        item.id for item in plan.items if item.kind is PlanItemKind.DECISION
    )
    dispatchable = tuple(
        item.model_copy(
            update={
                "dependencies": tuple(
                    dep for dep in item.dependencies if dep not in decision_ids
                )
            }
        )
        for item in plan.items
        if item.kind is PlanItemKind.WORK
    )
    tree = PlanTree.of(dispatchable)
    result = _level_from_items(
        tree.workstreams,
        plan=plan,
        objective=parent_task,
        tree=tree,
        parent_task_id=str(parent_task.id),
        depth=0,
    )
    # What an operator approved and what the org dispatches differ by the
    # decision items stripped here, and nothing else on this path says so.
    logger.info(
        DECOMPOSITION_REBUILT_FROM_PLAN,
        plan_id=str(plan.id),
        parent_task_id=str(parent_task.id),
        dispatched_items=len(dispatchable),
        decision_items=len(decision_ids),
        levels=result.max_depth_reached + 1,
        dependency_edges=sum(len(item.dependencies) for item in dispatchable),
    )
    return result


def _level_from_items(
    items: Sequence[PlanItem],
    *,
    plan: Plan,
    objective: Task,
    tree: PlanTree,
    parent_task_id: str,
    depth: int,
) -> DecompositionResult:
    """Rebuild one level of the tree, and every level beneath it.

    Args:
        items: The items at this level, in plan order.
        plan: The plan being dispatched.
        objective: The task the whole plan decomposes.
        tree: The plan's containment view.
        parent_task_id: What this level hangs off: the objective at the root,
            a container's task below it.
        depth: This level's nesting depth, zero at the root.

    Returns:
        The level, carrying a child result per item that was split.
    """
    return DecompositionResult(
        plan=DecompositionPlan(
            parent_task_id=NotBlankStr(parent_task_id),
            subtasks=tuple(_subtask_from_item(item, tree=tree) for item in items),
            task_structure=plan.task_structure,
            coordination_topology=plan.coordination_topology,
        ),
        created_tasks=tuple(
            task_from_item(
                item,
                plan=plan,
                objective=objective,
                parent_task_id=parent_task_id,
                tree=tree,
            )
            for item in items
        ),
        dependency_edges=tuple(
            (dep, item.id) for item in items for dep in item.dependencies
        ),
        depth=depth,
        children=tuple(
            _level_from_items(
                tree.children(item.id),
                plan=plan,
                objective=objective,
                tree=tree,
                parent_task_id=item.id,
                depth=depth + 1,
            )
            for item in items
            if tree.is_container(item.id)
        ),
    )
