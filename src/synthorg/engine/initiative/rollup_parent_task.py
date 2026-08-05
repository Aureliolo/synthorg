# module-kind: code
"""Walking the objective task to the status its plan items imply.

Coordination advances the parent once, when ``coordinate()`` returns, at
which point its children are typically still ``IN_REVIEW``: it can therefore
never land the parent's terminal status without reading an unverified run
outcome. The rollup re-derives it on the same recompute that already reads
persisted child status, which lets the parent finish honestly once the
review gate has ruled on every child.

Kept beside the rollup rather than inside it because it is a projection over
plan items with one rule of its own (see ``_OBJECTIVE_FINISHED_STATUSES``),
not part of the rollup's own state machine.
"""

from typing import Final

from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.parent_rollup import (
    advance_parent_to_rollup_status,
)
from synthorg.engine.decomposition.rollup import StatusRollup
from synthorg.engine.initiative.completion import ItemProgress
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger
from synthorg.observability.events.project import PROJECT_ROLLUP_SKIPPED

logger = get_logger(__name__)

#: Statuses that read as "the objective is over" on the board. The objective
#: outlives every individual item, so the parent walk may only land one of
#: these once the plan itself has delivered.
_OBJECTIVE_FINISHED_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.REJECTED,
    }
)


def parent_status_of(item: ItemProgress) -> TaskStatus:
    """Project one plan item onto the task status the parent rolls up.

    A ``DECISION`` item dispatches no task, so it contributes the status its
    resolution implies: ``COMPLETED`` once an option is recorded, and
    ``IN_PROGRESS`` while the choice is still open (it is real work the
    operator owes, so it must hold the parent open). A ``WORK`` item with no
    dispatched task yet is likewise still pending.

    Returns:
        The ``TaskStatus`` this item contributes to the parent rollup.
    """
    if item.kind is PlanItemKind.DECISION:
        return (
            TaskStatus.COMPLETED
            if item.chosen_option_id is not None
            else TaskStatus.IN_PROGRESS
        )
    return item.task_status if item.task_status is not None else TaskStatus.IN_PROGRESS


async def advance_objective_task(
    task_engine: TaskEngine | None,
    plan: Plan,
    items: tuple[ItemProgress, ...],
) -> None:
    """Walk the objective task to the status *items* imply.

    The objective task is the initiative on the board, so it is held open
    for exactly as long as the plan is: every item passing its own gate does
    not deliver the objective, the tail does, and one item failing does not
    end the objective while its siblings are still building. The walk
    therefore stops short of any finished-looking status until the plan
    itself is COMPLETED, while the rollup counts it records stay the
    children's real ones.

    A superseded plan is skipped entirely. Its successor owns the objective,
    and the replan that superseded it cancels the retired items, so deriving
    from them here would walk the objective task to a truly terminal
    CANCELLED that the successor could never reopen.

    Best-effort and idempotent, like the rest of the recompute: an
    unreachable target or a rejected hop is logged and repaired by the next
    event.
    """
    if task_engine is None or not items:
        return
    if plan.status is PlanStatus.SUPERSEDED:
        logger.debug(
            PROJECT_ROLLUP_SKIPPED,
            plan_id=str(plan.id),
            reason="superseded_plan_no_longer_owns_objective",
        )
        return
    live = await task_engine.get_task(plan.parent_task_id)
    if live is None:
        logger.debug(
            PROJECT_ROLLUP_SKIPPED,
            plan_id=str(plan.id),
            reason="parent_task_missing",
        )
        return
    rollup = StatusRollup.compute(
        NotBlankStr(plan.parent_task_id),
        tuple(parent_status_of(item) for item in items),
    )
    derived = rollup.derived_parent_status
    held = (
        derived in _OBJECTIVE_FINISHED_STATUSES
        and plan.status is not PlanStatus.COMPLETED
    )
    outcome = await advance_parent_to_rollup_status(
        task_engine,
        task_id=plan.parent_task_id,
        current_status=live.status,
        rollup=rollup,
        target=TaskStatus.IN_PROGRESS if held else derived,
    )
    if not outcome.success:
        logger.debug(
            PROJECT_ROLLUP_SKIPPED,
            plan_id=str(plan.id),
            reason="parent_walk_refused",
            note=outcome.error,
        )


__all__ = ["advance_objective_task", "parent_status_of"]
