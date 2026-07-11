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

from synthorg.core.artifact import ArtifactType, ExpectedArtifact
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)


def _expected_artifact(spec: NotBlankStr) -> ExpectedArtifact:
    """Project a free-text expected-artifact spec onto a typed declaration.

    The plan item carries the artifact as free text; the type is inferred from
    the path so the dispatched task's fail-loud zero-artifact guard has a typed
    declaration to check against, defaulting to ``CODE``.

    Returns:
        An :class:`ExpectedArtifact` with an inferred type and the spec as its
        path.
    """
    lowered = spec.lower()
    if "test" in lowered:
        artifact_type = ArtifactType.TESTS
    elif lowered.endswith(".md") or "doc" in lowered:
        artifact_type = ArtifactType.DOCUMENTATION
    else:
        artifact_type = ArtifactType.CODE
    return ExpectedArtifact(type=artifact_type, path=spec)


def _item_from_subtask(subtask: SubtaskDefinition) -> PlanItem:
    """Project one decomposition subtask onto a durable plan item.

    Returns:
        A :class:`PlanItem` carrying the subtask's identity, dependency
        edges, routing complexity/stakes, and owning role.
    """
    return PlanItem(
        id=subtask.id,
        title=subtask.title,
        description=subtask.description,
        dependencies=subtask.dependencies,
        owner=subtask.required_role,
        required_skills=subtask.required_skills,
        required_tags=subtask.required_tags,
        estimated_complexity=subtask.estimated_complexity,
        stakes=subtask.stakes,
    )


def plan_from_decomposition(  # noqa: PLR0913 -- decomposition + plan provenance fields
    result: DecompositionResult,
    *,
    project: NotBlankStr,
    objective_id: NotBlankStr,
    parent_task_id: NotBlankStr,
    created_at: datetime,
    status: PlanStatus = PlanStatus.PENDING_REVIEW,
    forecast_id: UUID | None = None,
) -> Plan:
    """Build a durable ``Plan`` from an executed ``DecompositionResult``.

    Args:
        result: The decomposition whose subtask tree becomes the plan items.
        project: Project the plan belongs to.
        objective_id: Charter/objective the plan serves.
        parent_task_id: The objective task that was decomposed.
        created_at: Timestamp stamped on both ``created_at`` and
            ``updated_at`` (a freshly built plan has never been revised).
        status: Initial lifecycle status (defaults to pending review, since
            a plan is built to be reviewed).
        forecast_id: Cost forecast released alongside the plan, if any.

    Returns:
        A validated :class:`Plan` mirroring the decomposition's structure.
    """
    items = tuple(_item_from_subtask(subtask) for subtask in result.plan.subtasks)
    return Plan(
        project=project,
        objective_id=objective_id,
        parent_task_id=parent_task_id,
        items=items,
        task_structure=result.plan.task_structure,
        coordination_topology=result.plan.coordination_topology,
        status=status,
        forecast_id=forecast_id,
        created_at=created_at,
        updated_at=created_at,
    )


def _subtask_from_item(item: PlanItem) -> SubtaskDefinition:
    """Project a durable plan item back onto a decomposition subtask.

    Routing hints the plan does not carry (skills, tags) default to empty; the
    item's ``owner`` maps back to the subtask's ``required_role``.

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
            _expected_artifact(a) for a in item.expected_artifacts
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
    subtasks = tuple(_subtask_from_item(item) for item in plan.items)
    created_tasks = tuple(
        _task_from_item(item, parent_task=parent_task) for item in plan.items
    )
    edges = tuple((dep, item.id) for item in plan.items for dep in item.dependencies)
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
