# module-kind: code
"""Every stage that mints a task of its own, in one place.

A stage job is the one row shape that carries a plan id and no item id, so any
caller asking "is this row somebody's stage job" has to ask it of every stage.
Asking it of a hand-written pair is what goes stale: the recovery sweep asked
only about the assembly task, so a skeleton job left running by a restart was
requeued by nothing, read as still running on every later pass, and parked its
plan for ever. The set is declared once and read from here, so a stage added
later is covered by everything that consults it rather than by whoever
remembers to widen a condition.

Kept apart from the binding modules themselves because they are deliberately
split by where their stage sits, and neither can import the other.
"""

from typing import Final

from synthorg.core.plan import Plan
from synthorg.core.task import Task
from synthorg.engine.initiative.head_stages import SKELETON_BINDING
from synthorg.engine.initiative.stage_state import StageBinding, is_stage_task
from synthorg.engine.initiative.tail_stages import INTEGRATION_BINDING

#: Every stage that mints a task. The evaluation stage is deliberately absent:
#: it runs as a bounded lead session and persists no row of its own, so there
#: is nothing here for a caller to recognise.
STAGE_BINDINGS: Final[tuple[StageBinding, ...]] = (
    SKELETON_BINDING,
    INTEGRATION_BINDING,
)


def is_any_stage_task(task: Task, plan: Plan) -> bool:
    """Whether *task* is a stage job *plan* minted, from any stage.

    Returns:
        ``True`` when the row was minted for this plan by one of the declared
        stages, which is decided on the minting actor rather than on the id
        shape alone, so a foreign row occupying a derived id is not mistaken
        for the stage's own work.
    """
    return any(is_stage_task(task, plan, binding=binding) for binding in STAGE_BINDINGS)


__all__ = ["STAGE_BINDINGS", "is_any_stage_task"]
