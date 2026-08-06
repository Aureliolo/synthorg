# module-kind: code
"""Refuse a task delete that would strand the plan built from it.

Deleting a task used to succeed while its decomposition was still running:
the run completed against the deleted row, produced a nine-item plan, and
parked it for approval under a task that 404s. The plan then could not be
removed either, because superseding it violated the items constraint and
there was no delete route.

The foreign key is the backstop, but an integrity error names a constraint
rather than a next step. Checking here means the operator is told which
plan holds the task and where to resolve it.
"""

from synthorg.api.state import AppState
from synthorg.core.domain_errors import PlanParentTaskInUseError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_TASK_DELETE_REFUSED
from synthorg.persistence.plan_protocol import PlanFilterSpec
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)


async def refuse_if_a_plan_owns_the_task(app_state: AppState, task_id: str) -> None:
    """Refuse the delete while a plan still names *task_id* as its objective.

    Args:
        app_state: Application state carrying persistence.
        task_id: The task the caller wants removed.

    Raises:
        PlanParentTaskInUseError: When any plan references the task.
    """
    plans = persistence_of(app_state).plans
    held = await plans.query(
        PlanFilterSpec(parent_task_id=NotBlankStr(task_id)),
        limit=1,
    )
    if not held:
        return
    plan_id = str(held[0].id)
    logger.info(API_TASK_DELETE_REFUSED, task_id=task_id, plan_id=plan_id)
    msg = (
        f"task {task_id} is the objective of plan {plan_id}; delete or "
        f"supersede that plan first (DELETE /plans/{plan_id})"
    )
    raise PlanParentTaskInUseError(msg)


__all__ = ["refuse_if_a_plan_owns_the_task"]
