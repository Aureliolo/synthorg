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

from synthorg.core.plan import Plan
from synthorg.core.plan_enums import DELETABLE_STATUSES
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_TASK_DELETE_REFUSED
from synthorg.persistence.plan_protocol import PlanFilterSpec, PlanRepository

logger = get_logger(__name__)


async def plan_blocking_delete(plans: PlanRepository, task_id: str) -> Plan | None:
    """Return the plan still naming *task_id* as its objective.

    The whole plan rather than its id, because its status decides whether
    the refusal has a next action to offer at all.

    Args:
        plans: The plan repository to look the reference up in.
        task_id: The task the caller wants removed.

    Returns:
        The blocking plan, or ``None`` when the delete is free to proceed.
    """
    held = await plans.query(
        PlanFilterSpec(parent_task_id=NotBlankStr(task_id)),
        limit=1,
    )
    if not held:
        return None
    blocking = held[0]
    logger.info(
        API_TASK_DELETE_REFUSED,
        task_id=task_id,
        plan_id=str(blocking.id),
        plan_status=blocking.status.value,
    )
    return blocking


def delete_refusal_message(task_id: str, plan: Plan) -> str:
    """Explain the refusal in terms of the operator's next action.

    Two refusals, and only one of them has a next action. A plan that never
    became work can be removed, so the message names that route. A decided
    plan cannot: it is the record of what was decided, its delivery
    verdicts hang off it, and ``DELETE /plans/{id}`` refuses it for exactly
    that reason. Pointing at that route anyway would send the operator to a
    409 and read as a bug rather than as a deliberate retention, so the
    message says the task stays instead of naming an action that fails.

    Returns:
        A message naming the plan and, when there is one, the route that
        resolves it.
    """
    plan_id = str(plan.id)
    if plan.status in DELETABLE_STATUSES:
        return (
            f"task {task_id} is the objective of plan {plan_id}; delete "
            f"that plan first (DELETE /plans/{plan_id})"
        )
    return (
        f"task {task_id} is the objective of plan {plan_id}, which is "
        f"{plan.status.value} and is retained with its delivery verdicts. "
        "A referenced task is not deletable while its plan is kept, and a "
        "decided plan is kept permanently."
    )


__all__ = ["delete_refusal_message", "plan_blocking_delete"]
