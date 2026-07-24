# module-kind: code
"""Pure derivation of initiative progress from plan items and task status.

The completion rule for a plan item:

- a ``WORK`` item is done when its dispatched task reached ``COMPLETED``;
- a ``DECISION`` item is done when an option has been chosen.

The ``WORK`` rule is where the verify gate composes into project completion.
Under the wired agent runtime a task reaches ``COMPLETED`` through
``ReviewGateService._apply_decision``, which runs the completion-oracle gate
chain, so requiring ``COMPLETED`` here means an initiative does not complete on
unverified work without any explicit oracle call in this module. Accepting
``IN_REVIEW`` (executed but not yet verified) would break that, which is why it
is called out in the tests as an invariant rather than an incidental branch.

That composition no longer depends on which writers are wired: the
lifecycle-only baseline execution service refuses to advance a plan-linked task
out of ``IN_REVIEW``, and the coordination parent rollup derives from persisted
task status rather than from execution outcomes, so ``COMPLETED`` on a
plan-linked task means the gate ran.

Every item being done is still not a delivered initiative, so this module never
derives ``COMPLETED`` for a plan: it opens the tail (INTEGRATING, then
EVALUATING) and those stages' own gates decide delivery.

Everything here is pure: no I/O, no clock, no repositories. The rollup service
supplies the inputs and owns the persistence.
"""

from enum import StrEnum
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.plan_enums import (
    TAIL_STATUSES,
    TERMINAL_STATUSES,
    PlanItemKind,
    PlanStatus,
)
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr

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

#: Statuses a task cannot leave on its own: nothing in the org will move it
#: without a new decision. Note what is absent. AWAITING_INPUT and AUTH_REQUIRED
#: are the org waiting on a human, and replanning would discard the question
#: rather than answer it; every other non-terminal status is work still moving.
_DEAD_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.FAILED,
        TaskStatus.REJECTED,
        TaskStatus.CANCELLED,
        TaskStatus.BLOCKED,
        TaskStatus.SUSPENDED,
        TaskStatus.INTERRUPTED,
    }
)

#: The dead statuses that mean the work was attempted and did not survive, as
#: opposed to never having got moving.
_DEAD_BY_FAILURE: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.FAILED, TaskStatus.REJECTED, TaskStatus.CANCELLED}
)

#: How a dispatched plan's status shows on its project. The tail mirrors
#: one-for-one so the cockpit distinguishes building from assembling from
#: scoring; a plan status absent here is not yet dispatched, and leaves the
#: project where it is.
_PLAN_TO_PROJECT_STATUS: Final[dict[PlanStatus, ProjectStatus]] = {
    PlanStatus.EXECUTING: ProjectStatus.ACTIVE,
    PlanStatus.INTEGRATING: ProjectStatus.INTEGRATING,
    PlanStatus.EVALUATING: ProjectStatus.EVALUATING,
    PlanStatus.COMPLETED: ProjectStatus.COMPLETED,
}

# The mirror is hand-maintained across three files, so it is checked at import
# rather than only by a test somebody has to remember to run: a tail stage
# added to PlanStatus without a project counterpart would otherwise park every
# project one stage behind its plan, silently.
if not _PLAN_TO_PROJECT_STATUS.keys() >= TAIL_STATUSES:
    _MISSING = sorted(s.value for s in TAIL_STATUSES - _PLAN_TO_PROJECT_STATUS.keys())
    _MIRROR_ERROR = f"tail statuses missing a project counterpart: {_MISSING}"
    raise ImportError(_MIRROR_ERROR)

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
    chosen_option_id: NotBlankStr | None = Field(
        default=None,
        description="Option recorded for a decision item",
    )

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> ItemProgress:
        """Reject a progress record carrying the other kind's fields.

        A DECISION item never dispatches a task and a WORK item never records
        a chosen option, so a record holding both is a construction mistake
        whose only symptom would be a silently wrong done-ness verdict.

        Returns:
            The validated model.

        Raises:
            ValueError: When the fields do not match ``kind``.
        """
        if self.kind is PlanItemKind.DECISION and (
            self.task_id is not None or self.task_status is not None
        ):
            msg = "A DECISION item carries no task"
            raise ValueError(msg)
        if self.kind is PlanItemKind.WORK and self.chosen_option_id is not None:
            msg = "A WORK item records no chosen option"
            raise ValueError(msg)
        return self


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


class StallReason(StrEnum):
    """Why an initiative can no longer advance on its own.

    Carried into the replan brief, so the successor is planned against what
    actually went wrong rather than a bare "it stopped".

    The first three are derived from item status by :func:`stall_reason`.
    ``ALL_FAILED``: every outstanding item was attempted and did not survive.
    ``BLOCKED``: every outstanding item is stuck behind something the org
    cannot clear itself. ``MIXED_DEAD``: some of each.

    The last two are tail-stage verdicts, invisible to any derivation over
    items (every item is done in both cases). ``INTEGRATION_FAILED``: the
    pieces were built but do not assemble into a working whole.
    ``EVALUATION_UNMET``: the whole runs but does not meet the objective.
    """

    ALL_FAILED = "all_failed"
    BLOCKED = "blocked"
    MIXED_DEAD = "mixed_dead"
    INTEGRATION_FAILED = "integration_failed"
    EVALUATION_UNMET = "evaluation_unmet"


#: The reasons :func:`stall_reason` can produce. A reason outside this set came
#: from a tail stage, so re-confirming it means re-reading that stage rather
#: than re-deriving over items (which would find nothing wrong).
ITEM_DERIVED_STALLS: Final[frozenset[StallReason]] = frozenset(
    {StallReason.ALL_FAILED, StallReason.BLOCKED, StallReason.MIXED_DEAD}
)


def stall_reason(items: tuple[ItemProgress, ...]) -> StallReason | None:
    """Classify whether a plan has run out of ways to advance.

    A plan is stalled when it has outstanding work and *none* of it can move
    without a new decision. That is a shape, not a duration, so there is no
    threshold to tune and no timer to run: the derivation is exact the moment
    the last live item dies.

    Three cases are deliberately not stalls, each because replanning would
    destroy something. Work still moving (CREATED through IN_REVIEW) is simply
    in flight. A human wait (AWAITING_INPUT, AUTH_REQUIRED) is the org waiting
    on the operator, and a replan would discard the question. An item whose
    task has not been persisted yet reads identically to one that will never
    dispatch, and dispatch writes the plan's EXECUTING status *before* it
    creates the task rows, so treating it as dead would replan every
    initiative during its own dispatch window.

    Returns:
        The reason, or ``None`` when the plan is done or can still advance.
    """
    outstanding = [item for item in items if not item_is_done(item)]
    if not outstanding:
        return None
    statuses = [item.task_status for item in outstanding]
    if not all(status in _DEAD_STATUSES for status in statuses):
        return None
    if all(status in _DEAD_BY_FAILURE for status in statuses):
        return StallReason.ALL_FAILED
    if not any(status in _DEAD_BY_FAILURE for status in statuses):
        return StallReason.BLOCKED
    return StallReason.MIXED_DEAD


def derive_plan_status(
    items: tuple[ItemProgress, ...],
    *,
    current: PlanStatus,
) -> PlanStatus:
    """Return the status *current* should advance to given its items.

    Every item being done ends EXECUTING and opens the tail, never the plan:
    a set of individually-verified pieces is not a delivered initiative, so
    the plan derives ``INTEGRATING`` and its own gate advances it from there.
    A plan already in the tail is left alone while its items hold, and falls
    back to ``EXECUTING`` the moment one regresses, which is what routes
    integration findings back as rework without a re-plan.

    An itemless plan never self-advances: it has delivered nothing, so
    "every item is done" is vacuously true and must not be treated as
    progress.

    Returns:
        ``INTEGRATING`` when a plan with items has every one done;
        ``EXECUTING`` when a tail-stage plan has an item no longer done;
        otherwise *current* unchanged (including for a terminal plan, which
        the rollup never reopens).
    """
    if current in TERMINAL_STATUSES:
        return current
    all_done = bool(items) and all(item_is_done(item) for item in items)
    if current in TAIL_STATUSES:
        return current if all_done else PlanStatus.EXECUTING
    if all_done:
        return PlanStatus.INTEGRATING
    return current


def derive_project_status(
    plan_status: PlanStatus,
    *,
    current: ProjectStatus,
) -> ProjectStatus:
    """Return the status *current* should advance to given its plan.

    A project follows the plan it is executing, with two guards: it never
    moves out of a terminal status, and it never moves while an operator has it
    ON_HOLD (resuming is the operator's call, so the rollup cannot finish work
    out from under a deliberate pause).

    A project whose plan is merely building derives ACTIVE. Dispatch already
    activates it, so this normally changes nothing; it matters when the project
    is behind its plan, and it is what lets the rollup walk such a project
    forward through ACTIVE rather than jumping it straight to COMPLETED. The
    tail statuses mirror one-for-one, so the cockpit shows which stage the
    initiative is actually in rather than flattening the tail into ACTIVE.

    Returns:
        The project status mirroring *plan_status*, or *current* unchanged when
        the plan is not yet dispatched.
    """
    if current in _ROLLUP_IMMUTABLE_PROJECT_STATUSES:
        return current
    mirrored = _PLAN_TO_PROJECT_STATUS.get(plan_status)
    return mirrored if mirrored is not None else current
