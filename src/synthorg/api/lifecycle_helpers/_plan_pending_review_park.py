# module-kind: code
"""What a plan at ``PENDING_REVIEW`` owes the operator: a decision they can take.

``PENDING_REVIEW`` is not a description of a plan, it is a promise that a
person will be asked. The approvals queue is the only surface that asks, and
the plans controller has no approve route, so a plan in that status with no
``plan:approve`` row is a promise nothing keeps: the plan page offers Rework,
Request changes and Delete, and the initiative simply stops.

Two paths reach the status and only one used to park anything. The first-time
gate parks its approval and states the rule in its own compensation ("without
an approval there is no route to approve or reject it"); the replan path
opened the same status through a different owner and parked nothing. This
module is the one place both build the rows from, so the promise and the thing
that keeps it cannot drift again.

Building is separate from writing on purpose: each caller compensates for a
failed write differently (the gate retires what it wrote and fails the shell;
the replan path fails the successor it just opened), and a helper that owned
the writes would have to own both compensations too.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

from synthorg.api.lifecycle_helpers._plan_approval_presentation import (
    plan_detail,
    plan_risk_level,
)
from synthorg.api.lifecycle_helpers.plan_questions import (
    PLAN_ID_METADATA_KEY,
    build_plan_questions,
)
from synthorg.approval.enums import ApprovalSource, ApprovalStatus
from synthorg.approval.plan_review import PLAN_APPROVAL_ACTION_TYPE
from synthorg.core.approval import ApprovalItem
from synthorg.core.plan import Plan
from synthorg.core.plan_tree import PlanTree
from synthorg.core.types import NotBlankStr

#: ``ApprovalItem.metadata`` key carrying the project the plan belongs to.
PROJECT_METADATA_KEY = "project"


def plan_approval_item(
    *,
    plan_id: str,
    titles: Sequence[str],
    total_units: int,
    objective_title: str,
    project: str,
    task_id: NotBlankStr,
    requested_by: NotBlankStr,
    now: datetime,
) -> ApprovalItem:
    """Build the one approval that ``PENDING_REVIEW`` promises.

    Args:
        plan_id: The durable plan the approval decides.
        titles: One title per workstream the plan proposes, for the summary.
        total_units: How many units the whole plan holds, which is what the
            risk scale reads: the approval commits every level, not the top.
        objective_title: What the plan is for, as the operator reads it.
        project: The project the plan belongs to.
        task_id: The objective task, which the approvals surface links by.
        requested_by: The actor recorded as asking.
        now: Creation time.

    Returns:
        The pending ``plan:approve`` item.
    """
    return ApprovalItem(
        id=uuid4(),
        action_type=NotBlankStr(PLAN_APPROVAL_ACTION_TYPE),
        title=NotBlankStr(f"Approve plan for: {objective_title}"),
        description=NotBlankStr(plan_detail(titles, total_units=total_units)),
        requested_by=requested_by,
        risk_level=plan_risk_level(total_units),
        source=ApprovalSource.PLAN_REVIEW,
        status=ApprovalStatus.PENDING,
        created_at=now,
        task_id=task_id,
        metadata={
            PLAN_ID_METADATA_KEY: plan_id,
            PROJECT_METADATA_KEY: project,
        },
    )


def review_approvals_for(
    plan: Plan,
    *,
    requested_by: NotBlankStr,
    now: datetime,
) -> tuple[ApprovalItem, ...]:
    """Every row a persisted plan needs before a person can decide it.

    The approval first, then the plan's open questions, in the order the
    caller must write them: a question parked against a plan the operator
    cannot reach is the same defect one level down.

    Args:
        plan: The persisted plan, already at ``PENDING_REVIEW``.
        requested_by: The actor recorded as asking.
        now: Creation time.

    Returns:
        The approval, followed by one item per open question.
    """
    task_id = NotBlankStr(str(plan.parent_task_id))
    approval = plan_approval_item(
        plan_id=str(plan.id),
        titles=[str(item.title) for item in PlanTree.of(plan.items).workstreams],
        total_units=len(plan.items),
        objective_title=str(plan.objective_title),
        project=str(plan.project),
        task_id=task_id,
        requested_by=requested_by,
        now=now,
    )
    questions = build_plan_questions(
        plan, task_id=task_id, requested_by=requested_by, now=now
    )
    return (approval, *questions)


__all__ = [
    "PROJECT_METADATA_KEY",
    "plan_approval_item",
    "review_approvals_for",
]
