# module-kind: code
"""Read a plan's live item progress from persisted task status.

The bridge between the durable plan (items) and the work implementing it
(tasks). Kept apart from both the rollup and the replan trigger because both
need exactly this view and neither should own it: the trigger runs detached,
long after the rollup derived its own snapshot, so it must re-read rather than
trust a value passed across that gap.

Reads persisted status only, never run outcomes, for the reason spelled out in
:mod:`synthorg.engine.initiative.completion`: a run that finished is not a run
that was verified.
"""

from typing import Final
from uuid import UUID

from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.task import Task
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.initiative.completion import ItemProgress
from synthorg.engine.task_engine_apply_helpers import TRULY_TERMINAL_STATUSES
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.task_protocol import TaskFilterSpec

#: Page size for draining a plan's tasks. A plan's item count is bounded well
#: below this at the request boundary, so one page is the normal case and the
#: loop is a guard rather than an expected path.
TASK_PAGE_SIZE: Final[int] = 200


async def tasks_by_item(
    persistence: PersistenceBackend,
    plan: Plan,
) -> dict[UUID, Task]:
    """Index the plan's dispatched tasks by the item each implements.

    Tasks carrying no ``plan_item_id`` are deliberately dropped: they belong to
    the plan without implementing one of its items (the integration task is
    one), so counting them would distort every derivation over plan items.

    Returns:
        Map of plan-item id to the task implementing it.
    """
    indexed: dict[UUID, Task] = {}
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded by plan size
    while True:
        page = await persistence.tasks.query(
            TaskFilterSpec(plan=plan.id),
            limit=TASK_PAGE_SIZE,
            offset=offset,
        )
        for task in page:
            if task.plan_item_id is not None:
                indexed[task.plan_item_id] = task
        if len(page) < TASK_PAGE_SIZE:
            return indexed
        offset += TASK_PAGE_SIZE


async def count_live_tasks(
    persistence: PersistenceBackend,
    plan: Plan,
) -> int:
    """Count the plan's tasks that have not reached a terminal status.

    Answers "are this plan's items actually building", which the delete guard
    used to assert from the plan's status alone. A dispatch that died before
    it wrote a task row leaves an EXECUTING plan with nothing building, and
    the operator was told to replan work that does not exist.

    Every task of the plan counts, including one implementing no item (the
    integration task): a plan with an assembly job still running is building
    just as much as one with a work item running.

    Returns:
        The number of non-terminal tasks belonging to *plan*.
    """
    live = 0
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded by plan size
    while True:
        page = await persistence.tasks.query(
            TaskFilterSpec(plan=plan.id),
            limit=TASK_PAGE_SIZE,
            offset=offset,
        )
        live += sum(1 for task in page if task.status not in TRULY_TERMINAL_STATUSES)
        if len(page) < TASK_PAGE_SIZE:
            return live
        offset += TASK_PAGE_SIZE


async def collect_item_progress(
    persistence: PersistenceBackend,
    plan: Plan,
) -> tuple[ItemProgress, ...]:
    """Pair each plan item with the live status of its dispatched task.

    Returns:
        One :class:`ItemProgress` per plan item, in plan order.
    """
    by_item = await tasks_by_item(persistence, plan)
    progress: list[ItemProgress] = []
    for item in plan.items:
        item_uuid = subtask_uuid(item.id)
        task = by_item.get(item_uuid)
        is_decision = item.kind is PlanItemKind.DECISION
        progress.append(
            ItemProgress(
                item_id=item_uuid,
                kind=item.kind,
                task_id=task.id if task is not None else None,
                task_status=task.status if task is not None else None,
                chosen_option_id=item.chosen_option_id if is_decision else None,
                has_options=bool(item.options) if is_decision else False,
            )
        )
    return tuple(progress)
