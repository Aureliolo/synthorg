# module-kind: code
"""Pure derivation of initiative progress from plan items and task status.

The completion rule for a plan item:

- a ``WORK`` item is done when its dispatched task reached ``COMPLETED``;
- a ``DECISION`` item is done when an option has been chosen.

The ``WORK`` rule is where the verify gate composes into project completion. A
task only reaches ``COMPLETED`` through ``ReviewGateService._apply_decision``,
which runs the completion-oracle gate chain, so requiring ``COMPLETED`` here
means an initiative cannot complete on unverified work without any explicit
oracle call in this module. Accepting ``IN_REVIEW`` (executed but not yet
verified) would break that guarantee, which is why it is called out in the
tests as an invariant rather than an incidental branch.

Everything here is pure: no I/O, no clock, no repositories. The rollup service
supplies the inputs and owns the persistence.
"""

from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.plan_enums import (
    TERMINAL_STATUSES,
    PlanItemKind,
    PlanStatus,
)
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task_enums import TaskStatus

#: Task statuses that count as work needing operator attention. Neither is a
#: lifecycle state for the plan or project; both surface as derived counts.
_FAILED_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.FAILED, TaskStatus.REJECTED}
)
_BLOCKED_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.BLOCKED,
        TaskStatus.AWAITING_INPUT,
        TaskStatus.AUTH_REQUIRED,
        TaskStatus.SUSPENDED,
        TaskStatus.INTERRUPTED,
    }
)

#: Project statuses the rollup will not move away from. COMPLETED and
#: CANCELLED are terminal; ON_HOLD is a deliberate operator pause that the
#: rollup must not finish work out from under.
_ROLLUP_IMMUTABLE_PROJECT_STATUSES: Final[frozenset[ProjectStatus]] = frozenset(
    {
        ProjectStatus.COMPLETED,
        ProjectStatus.CANCELLED,
        ProjectStatus.ON_HOLD,
    }
)


class ItemProgress(BaseModel):
    """One plan item paired with the live state of its dispatched task.

    Attributes:
        item_id: The plan item's id.
        kind: Whether the item is executed work or a recorded decision.
        task_id: The task implementing a ``WORK`` item (``None`` for a
            ``DECISION`` item, which never dispatches, or for a work item
            whose task is not yet found).
        task_status: The task's persisted status, post verify gate.
        chosen_option_id: The option recorded for a ``DECISION`` item.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    item_id: UUID = Field(description="Plan item identifier")
    kind: PlanItemKind = Field(description="Work or decision")
    task_id: UUID | None = Field(
        default=None,
        description="Task implementing this item",
    )
    task_status: TaskStatus | None = Field(
        default=None,
        description="Persisted status of the implementing task",
    )
    chosen_option_id: str | None = Field(
        default=None,
        description="Option recorded for a decision item",
    )


class ProgressSummary(BaseModel):
    """Derived counts across a plan's items.

    Attributes:
        total: Number of plan items.
        done: Items satisfying the completion rule.
        failed: Work items whose task failed or was rejected.
        blocked: Work items whose task is stalled awaiting something.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    total: int = Field(default=0, ge=0, description="Number of plan items")
    done: int = Field(default=0, ge=0, description="Items that are done")
    failed: int = Field(default=0, ge=0, description="Items whose task failed")
    blocked: int = Field(default=0, ge=0, description="Items whose task stalled")


def item_is_done(item: ItemProgress) -> bool:
    """Return whether *item* satisfies the completion rule.

    Returns:
        ``True`` when a ``WORK`` item's task reached ``COMPLETED`` or a
        ``DECISION`` item has a chosen option; ``False`` otherwise.
    """
    if item.kind is PlanItemKind.DECISION:
        return item.chosen_option_id is not None
    return item.task_status is TaskStatus.COMPLETED


def summarise_progress(items: tuple[ItemProgress, ...]) -> ProgressSummary:
    """Count done, failed, and blocked items across a plan.

    Returns:
        A :class:`ProgressSummary` over *items*.
    """
    return ProgressSummary(
        total=len(items),
        done=sum(1 for item in items if item_is_done(item)),
        failed=sum(1 for item in items if item.task_status in _FAILED_STATUSES),
        blocked=sum(1 for item in items if item.task_status in _BLOCKED_STATUSES),
    )


def derive_plan_status(
    items: tuple[ItemProgress, ...],
    *,
    current: PlanStatus,
) -> PlanStatus:
    """Return the status *current* should advance to given its items.

    An itemless plan never self-completes: it has delivered nothing, so
    "every item is done" is vacuously true and must not be treated as
    success.

    Returns:
        ``COMPLETED`` when the plan has items and every one is done;
        otherwise *current* unchanged (including for a terminal plan, which
        the rollup never reopens).
    """
    if current in TERMINAL_STATUSES:
        return current
    if items and all(item_is_done(item) for item in items):
        return PlanStatus.COMPLETED
    return current


def derive_project_status(
    plan_status: PlanStatus,
    *,
    current: ProjectStatus,
) -> ProjectStatus:
    """Return the status *current* should advance to given its plan.

    A project follows the plan it is executing, with two guards: it never
    moves out of a terminal status, and it never completes while an operator
    has it ON_HOLD (resuming is the operator's call, so the rollup cannot
    finish work out from under a deliberate pause).

    Returns:
        ``COMPLETED`` when the plan completed and the project is live;
        otherwise *current* unchanged.
    """
    if current in _ROLLUP_IMMUTABLE_PROJECT_STATUSES:
        return current
    if plan_status is PlanStatus.COMPLETED:
        return ProjectStatus.COMPLETED
    return current
