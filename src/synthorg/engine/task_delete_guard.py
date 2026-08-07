# module-kind: code
"""Refuse a task delete that would strand the plan built from it.

Deleting a task used to succeed while its decomposition was still
running: the run completed against the deleted row, produced a nine-item
plan, and parked it for approval under a task that 404s. The plan then
could not be removed either, because superseding it violated the items
constraint and there was no delete route.

The foreign key is the backstop, but an integrity error names a
constraint rather than a next step. Checking here means the operator is
told which plan holds the task and where to resolve it.

This lives in the engine, on the one path every delete takes, rather
than in the REST controller. Three callers reach ``delete_task``: the
controller, the MCP handler, and the async-task compensating rollback.
A controller-side check covered one of them, so the other two got a
generic retryable 500 with no plan id and nothing to act on.
"""

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_TASK_DELETE_REFUSED
from synthorg.persistence.plan_protocol import PlanFilterSpec, PlanRepository

logger = get_logger(__name__)


async def plan_blocking_delete(plans: PlanRepository, task_id: str) -> str | None:
    """Return the id of the plan still naming *task_id* as its objective.

    Args:
        plans: The plan repository to look the reference up in.
        task_id: The task the caller wants removed.

    Returns:
        The blocking plan's id, or ``None`` when the delete is free to
        proceed.
    """
    held = await plans.query(
        PlanFilterSpec(parent_task_id=NotBlankStr(task_id)),
        limit=1,
    )
    if not held:
        return None
    plan_id = str(held[0].id)
    logger.info(API_TASK_DELETE_REFUSED, task_id=task_id, plan_id=plan_id)
    return plan_id


def delete_refusal_message(task_id: str, plan_id: str) -> str:
    """Explain the refusal in terms of the operator's next action.

    Returns:
        A message naming the plan and the route that resolves it.
    """
    return (
        f"task {task_id} is the objective of plan {plan_id}; delete or "
        f"supersede that plan first (DELETE /plans/{plan_id})"
    )


__all__ = ["delete_refusal_message", "plan_blocking_delete"]
