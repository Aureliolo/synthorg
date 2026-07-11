# module-kind: code
"""Map a transient ``DecompositionResult`` onto a durable ``Plan``.

The decomposition layer produces a ``DecompositionResult`` (the executed
subtask tree). To make a plan first-class (reviewable, revisable, and
outliving the approval decision), that transient shape is projected onto the
persisted :class:`~synthorg.core.plan.Plan` entity. This module owns the
projection so both the plan-review gate and any future re-plan path build a
``Plan`` the same way.
"""

from datetime import datetime
from uuid import UUID

from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionResult,
    SubtaskDefinition,
)


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
