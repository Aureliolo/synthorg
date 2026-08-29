# module-kind: code
"""What the recovery sweep reads, paged.

Kept apart from the sweep itself because these answer a different question. The
reconciler decides what a plan needs; this decides which rows it is deciding
about, and both readers page for the same reason: a deployment accumulates rows
for its whole life, so an unpaged read makes each pass cost what the deployment
has ever done rather than what it still owes.
"""

from collections.abc import Sequence

from synthorg.core.plan import Plan
from synthorg.core.plan_enums import TERMINAL_STATUSES, PlanStatus
from synthorg.core.task import Task
from synthorg.engine.initiative.item_progress import TASK_PAGE_SIZE
from synthorg.persistence.plan_protocol import PlanFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.task_protocol import TaskFilterSpec


async def unfinished_plans(persistence: PersistenceBackend) -> Sequence[Plan]:
    """Read every plan that has not reached a terminal status.

    Asked per unfinished status rather than by reading every row and discarding
    the terminal ones, because terminal plans only accumulate. The filter is a
    single status, so the set is DERIVED from the enum minus the terminal ones:
    a member added later is swept because it is not terminal, which is the
    opposite default from a hand-listed set that would silently stop covering
    it.

    Args:
        persistence: Backend supplying the plan repository.

    Returns:
        The unfinished plans, oldest page first within each status.
    """
    found: list[Plan] = []
    for status in sorted(set(PlanStatus) - TERMINAL_STATUSES):
        found.extend(await plans_with_status(persistence, status))
    return found


async def plans_with_status(
    persistence: PersistenceBackend, status: PlanStatus
) -> Sequence[Plan]:
    """Page through every plan currently at *status*.

    Args:
        persistence: Backend supplying the plan repository.
        status: The lifecycle status to enumerate.

    Returns:
        The matching plans, oldest page first.
    """
    spec = PlanFilterSpec(status=status)
    found: list[Plan] = []
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded by plan count
    while True:
        page = await persistence.plans.query(spec, limit=TASK_PAGE_SIZE, offset=offset)
        found.extend(page)
        if len(page) < TASK_PAGE_SIZE:
            return found
        offset += TASK_PAGE_SIZE


async def plan_tasks(persistence: PersistenceBackend, plan: Plan) -> Sequence[Task]:
    """Read every task filed against *plan*.

    Args:
        persistence: Backend supplying the task repository.
        plan: The plan whose rows to read.

    Returns:
        The plan's tasks.
    """
    found: list[Task] = []
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded by plan size
    while True:
        page = await persistence.tasks.query(
            TaskFilterSpec(plan=plan.id), limit=TASK_PAGE_SIZE, offset=offset
        )
        found.extend(page)
        if len(page) < TASK_PAGE_SIZE:
            return found
        offset += TASK_PAGE_SIZE


__all__ = ["plan_tasks", "plans_with_status", "unfinished_plans"]
