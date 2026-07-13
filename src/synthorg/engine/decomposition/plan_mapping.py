# module-kind: code
"""Project between a transient ``DecompositionResult`` and a durable ``Plan``.

The decomposition layer produces a ``DecompositionResult`` (the executed
subtask tree). To make a plan first-class (reviewable, revisable, and
outliving the approval decision), that transient shape is projected onto the
persisted :class:`~synthorg.core.plan.Plan` entity, and back again at dispatch
time so an operator-edited plan is the one that actually builds. This module
owns both directions so the gate, the API, and the resume path stay in step.
"""

from datetime import datetime
from uuid import UUID

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


def plan_from_decomposition(  # noqa: PLR0913 -- decomposition + plan provenance fields
    result: DecompositionResult,
    *,
    project: NotBlankStr,
    objective_id: NotBlankStr,
    objective_title: NotBlankStr,
    parent_task_id: NotBlankStr,
    created_at: datetime,
    status: PlanStatus = PlanStatus.PENDING_REVIEW,
    forecast_id: UUID | None = None,
    review: PlanReview | None = None,
    objective_criteria: tuple[NotBlankStr, ...] = (),
) -> Plan:
    """Build a durable ``Plan`` from an executed ``DecompositionResult``.

    Args:
        result: The decomposition whose subtask tree becomes the plan items.
        project: Project the plan belongs to.
        objective_id: Charter/objective the plan serves.
        objective_title: Human title of the objective, denormalised onto the
            plan so the review surface never shows a raw id.
        parent_task_id: The objective task that was decomposed.
        created_at: Timestamp stamped on both ``created_at`` and
            ``updated_at`` (a freshly built plan has never been revised).
        status: Initial lifecycle status (defaults to pending review, since
            a plan is built to be reviewed).
        forecast_id: Cost forecast released alongside the plan, if any.
        review: The consolidated stakeholder-panel review, if the plan was
            reviewed before parking (``None`` when no panel ran).
        objective_criteria: The objective's acceptance criteria, denormalised
            onto the plan so the coverage map can flag any criterion no item
            advances (empty when the objective declared none).

    Returns:
        A validated :class:`Plan` mirroring the decomposition's structure.
    """
    items = tuple(_item_from_subtask(subtask) for subtask in result.plan.subtasks)
    return Plan(
        project=project,
        objective_id=objective_id,
        objective_title=objective_title,
        parent_task_id=parent_task_id,
        items=items,
        task_structure=result.plan.task_structure,
        coordination_topology=result.plan.coordination_topology,
        status=status,
        forecast_id=forecast_id,
        review=review,
        objective_criteria=objective_criteria,
        open_questions=result.plan.open_questions,
        assumptions=result.plan.assumptions,
        created_at=created_at,
        updated_at=created_at,
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


def _task_from_item(item: PlanItem, *, parent_task: Task) -> Task:
    """Rebuild the child task for a plan item under *parent_task*.

    Uses the same deterministic id mapping as the decomposition service, so a
    re-dispatch of the same (possibly edited) plan targets stable task ids.

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
        _task_from_item(item, parent_task=parent_task) for item in dispatchable
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
