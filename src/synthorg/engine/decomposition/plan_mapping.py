# module-kind: code
"""Project between a transient ``DecompositionResult`` and a durable ``Plan``.

The decomposition layer produces a ``DecompositionResult`` (the executed
subtask tree). To make a plan first-class (reviewable, revisable, and
outliving the approval decision), that transient shape is projected onto the
persisted :class:`~synthorg.core.plan.Plan` entity, and back again at dispatch
time so an operator-edited plan is the one that actually builds. This module
owns both directions so the gate, the API, and the resume path stay in step.
"""

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.plan_review import PlanReview
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._artifacts import expected_artifact_from_spec
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)


class PlanProvenance(BaseModel):
    """The plan-level provenance a durable ``Plan`` carries beyond its items.

    Bundles the objective/project identity, timing, lifecycle, and denormalised
    review context so :func:`plan_from_decomposition` takes one typed argument
    rather than a long provenance parameter list.

    Attributes:
        project: Project the plan belongs to.
        objective_id: Charter/objective the plan serves.
        objective_title: Human title of the objective, denormalised onto the
            plan so the review surface never shows a raw id.
        parent_task_id: The objective task that was decomposed.
        created_at: Timestamp stamped on both ``created_at`` and ``updated_at``
            (a freshly built plan has never been revised).
        status: Initial lifecycle status (defaults to pending review).
        forecast_id: Cost forecast released alongside the plan, if any.
        review: The consolidated stakeholder-panel review, if a panel ran.
        objective_criteria: The objective's acceptance criteria, denormalised
            onto the plan so the coverage map can flag any uncovered criterion.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project: NotBlankStr = Field(description="Project the plan belongs to")
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
    objective_criteria: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="The objective's acceptance criteria, denormalised",
    )


def _item_from_subtask(subtask: SubtaskDefinition) -> PlanItem:
    """Project one decomposition subtask onto a durable plan item.

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
    )


def items_from_decomposition(result: DecompositionResult) -> tuple[PlanItem, ...]:
    """Project a decomposition's subtask tree onto durable plan items.

    Split out from :func:`plan_from_decomposition` because a re-plan needs the
    items alone: its successor keeps the retired plan's provenance and is built
    by the plan service, not assembled here.

    Returns:
        One :class:`PlanItem` per subtask, in plan order.
    """
    return tuple(_item_from_subtask(subtask) for subtask in result.plan.subtasks)


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
    """
    items = items_from_decomposition(result)
    return Plan(
        project=provenance.project,
        objective_id=provenance.objective_id,
        objective_title=provenance.objective_title,
        parent_task_id=provenance.parent_task_id,
        items=items,
        task_structure=result.plan.task_structure,
        coordination_topology=result.plan.coordination_topology,
        status=provenance.status,
        forecast_id=provenance.forecast_id,
        review=provenance.review,
        objective_criteria=provenance.objective_criteria,
        open_questions=result.plan.open_questions,
        assumptions=result.plan.assumptions,
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


def _subtask_from_item(item: PlanItem) -> SubtaskDefinition:
    """Project a durable plan item back onto a decomposition subtask.

    The item's ``owner`` maps back to the subtask's ``required_role``, and the
    per-item acceptance criteria and expected artifacts round-trip so a
    re-decomposition off a durable plan keeps the guard armed.

    Returns:
        A :class:`SubtaskDefinition` mirroring the plan item.
    """
    return SubtaskDefinition(
        id=item.id,
        title=item.title,
        description=item.description,
        dependencies=item.dependencies,
        estimated_complexity=item.estimated_complexity,
        stakes=item.stakes,
        required_skills=item.required_skills,
        required_tags=item.required_tags,
        required_role=item.owner,
        expected_artifacts=item.expected_artifacts,
        acceptance_criteria=item.acceptance_criteria,
        kind=item.kind,
        options=item.options,
        satisfies=item.satisfies,
    )


def _task_from_item(item: PlanItem, *, plan_id: UUID, parent_task: Task) -> Task:
    """Rebuild the child task for a plan item under *parent_task*.

    Uses the same deterministic id mapping as the decomposition service, so a
    re-dispatch of the same (possibly edited) plan targets stable task ids.

    The plan and item ids are stamped onto the task so the rollup can query a
    plan's tasks directly, rather than re-deriving the id mapping at every
    call site.

    Returns:
        A ``CREATED`` child :class:`Task` inheriting the parent's routing
        context (type, priority, project, delegation chain) and carrying the
        item's acceptance criteria and expected artifacts, so the task's
        fail-loud zero-artifact guard engages on the plan-review dispatch path.
    """
    return Task(
        id=subtask_uuid(item.id),
        title=item.title,
        description=item.description,
        type=parent_task.type,
        priority=parent_task.priority,
        project=parent_task.project,
        plan_id=plan_id,
        plan_item_id=subtask_uuid(item.id),
        created_by=parent_task.created_by,
        parent_task_id=str(parent_task.id),
        delegation_chain=parent_task.delegation_chain,
        dependencies=item.dependencies,
        acceptance_criteria=tuple(
            AcceptanceCriterion(description=c) for c in item.acceptance_criteria
        ),
        artifacts_expected=tuple(
            expected_artifact_from_spec(a) for a in item.expected_artifacts
        ),
        status=TaskStatus.CREATED,
        estimated_complexity=item.estimated_complexity,
        stakes=item.stakes,
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

    Args:
        plan: The durable plan to dispatch (its items become the subtasks).
        parent_task: The objective task the plan decomposes; supplies the
            routing context inherited by each child task.

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
    subtasks = tuple(_subtask_from_item(item) for item in dispatchable)
    created_tasks = tuple(
        _task_from_item(item, plan_id=plan.id, parent_task=parent_task)
        for item in dispatchable
    )
    edges = tuple((dep, item.id) for item in dispatchable for dep in item.dependencies)
    decomposition_plan = DecompositionPlan(
        parent_task_id=str(parent_task.id),
        subtasks=subtasks,
        task_structure=plan.task_structure,
        coordination_topology=plan.coordination_topology,
    )
    return DecompositionResult(
        plan=decomposition_plan,
        created_tasks=created_tasks,
        dependency_edges=edges,
    )
