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

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.plan_enums import (
    HEAD_STATUSES,
    STAGE_STATUSES,
    TAIL_STATUSES,
    TERMINAL_STATUSES,
    PlanItemKind,
    PlanStatus,
)
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task_enums import (
    ATTENDED_BLOCKED_REASONS,
    BlockedReason,
    TaskStatus,
)
from synthorg.core.types import NotBlankStr
from synthorg.core.validation import set_field_names

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
#:
#: SKELETON maps to ACTIVE rather than to a project status of its own. Writing
#: the contract IS work on the project, and the operator's question at this
#: point is answered by the plan page rather than by a fourth project state:
#: the tail's own states earn theirs because a project can sit in them for a
#: long time with nothing else running, which is not true here.
_PLAN_TO_PROJECT_STATUS: Final[dict[PlanStatus, ProjectStatus]] = {
    PlanStatus.SKELETON: ProjectStatus.ACTIVE,
    PlanStatus.EXECUTING: ProjectStatus.ACTIVE,
    PlanStatus.INTEGRATING: ProjectStatus.INTEGRATING,
    PlanStatus.EVALUATING: ProjectStatus.EVALUATING,
    PlanStatus.COMPLETED: ProjectStatus.COMPLETED,
    # The failure direction, which had the same hole the comment below guards
    # against: a plan that failed left its project reading PLANNING for ever,
    # so a dead initiative was indistinguishable from one still being planned
    # and nothing derived it an exit.
    PlanStatus.FAILED: ProjectStatus.FAILED,
}

# The mirror is hand-maintained across three files, so it is checked at import
# rather than only by a test somebody has to remember to run: a stage added to
# PlanStatus without a project counterpart would otherwise park every project
# one stage behind its plan, silently. Every stage is covered, head as well as
# tail: the head stage was added later and would have been the first thing this
# check did not watch.
if not _PLAN_TO_PROJECT_STATUS.keys() >= STAGE_STATUSES:
    _MISSING = sorted(s.value for s in STAGE_STATUSES - _PLAN_TO_PROJECT_STATUS.keys())
    _MIRROR_ERROR = f"stage statuses missing a project counterpart: {_MISSING}"
    raise ImportError(_MIRROR_ERROR)

#: Project statuses the rollup will not move away from. COMPLETED and
#: CANCELLED are terminal; ON_HOLD is a deliberate operator pause that the
#: rollup must not finish work out from under. FAILED is deliberately ABSENT:
#: a retry is a fresh plan against the same project, so the project has to be
#: able to come back, and pinning it would trade one dead end for another.
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
        blocked_reason: Why a BLOCKED task is blocked. Read because BLOCKED
            alone does not say whether anything in the org will move the row:
            some reasons wait on a sweep, one waits on the operator.
        chosen_option_id: The option recorded for a ``DECISION`` item.
        has_options: Whether a ``DECISION`` item offers anything to choose
            between. An undecided item with none can be resolved by nobody,
            which is the difference between waiting on a human and being
            stuck; always ``False`` for a ``WORK`` item.
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
    blocked_reason: BlockedReason | None = Field(
        default=None,
        description="Why the implementing task is blocked, when it is",
    )
    chosen_option_id: NotBlankStr | None = Field(
        default=None,
        description="Option recorded for a decision item",
    )
    has_options: bool = Field(
        default=False,
        description="Whether a decision item offers options to choose from",
    )

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> ItemProgress:
        """Reject a progress record carrying the other kind's fields.

        A DECISION item never dispatches a task and a WORK item never records
        a chosen option or carries options, so a record holding both is a
        construction mistake whose only symptom would be a silently wrong
        done-ness verdict.

        Returns:
            The validated model.

        Raises:
            ValueError: When the fields do not match ``kind``.
        """
        if self.kind is PlanItemKind.DECISION and (
            offending := set_field_names(
                task_id=self.task_id, task_status=self.task_status
            )
        ):
            msg = f"A DECISION item carries no task, but {offending} is set"
            raise ValueError(msg)
        if self.kind is PlanItemKind.WORK and (
            offending := set_field_names(
                chosen_option_id=self.chosen_option_id,
                has_options=self.has_options or None,
            )
        ):
            msg = f"A WORK item records no decision, but {offending} is set"
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

    The last three are stage verdicts, invisible to any derivation over items.
    ``SKELETON_FAILED``: the contract could not be written as code, which is a
    statement about the plan rather than about the agent that tried, and it is
    invisible for the opposite reason to the other two: no item has been
    dispatched yet, so there is nothing to derive from at all.
    ``INTEGRATION_FAILED``: the pieces were built but do not assemble into a
    working whole. ``EVALUATION_UNMET``: the whole runs but does not meet the
    objective. In both of those every item is done, which is why an
    item-derived stall cannot see them either.
    """

    ALL_FAILED = "all_failed"
    BLOCKED = "blocked"
    MIXED_DEAD = "mixed_dead"
    SKELETON_FAILED = "skeleton_failed"
    INTEGRATION_FAILED = "integration_failed"
    EVALUATION_UNMET = "evaluation_unmet"


#: The reasons :func:`stall_reason` can produce. A reason outside this set came
#: from a tail stage, so re-confirming it means re-reading that stage rather
#: than re-deriving over items (which would find nothing wrong).
ITEM_DERIVED_STALLS: Final[frozenset[StallReason]] = frozenset(
    {StallReason.ALL_FAILED, StallReason.BLOCKED, StallReason.MIXED_DEAD}
)

#: The stage each stage-derived verdict came from. A plan that has left that
#: stage has been dealt with (a human replanned it, or the stage re-ran), so
#: the verdict is stale.
#:
#: Declared here, beside the set that decides which branch applies, because
#: every re-confirmation of a stall needs both halves and two copies of the
#: pairing is two answers to "is this plan still stalled". Reading only the
#: item half answers ``None`` for every tail-stage stall, which reads as
#: "recovered" and silently drops the decision.
STAGE_OF_STALL_REASON: Final[Mapping[StallReason, PlanStatus]] = MappingProxyType(
    {
        StallReason.SKELETON_FAILED: PlanStatus.SKELETON,
        StallReason.INTEGRATION_FAILED: PlanStatus.INTEGRATING,
        StallReason.EVALUATION_UNMET: PlanStatus.EVALUATING,
    }
)


class ReplanDisposition(StrEnum):
    """What the replan trigger did with a stall it was asked to consider.

    "Does this initiative have a way forward" is one decision, and the trigger
    is its owner: it holds the master switch and the generation cap, so a
    caller deciding from the trigger's mere presence is a second authority
    that cannot see either refusal. This is the answer travelling back, so the
    caller routes on what happened rather than on what it assumed.

    ``SCHEDULED``: a detached replan started. ``ALREADY_RUNNING``: one is in
    flight for this plan, so this ask collapses into it. ``UNAVAILABLE``: the
    trigger could not start work at this moment (the process is stopping, or
    the spawn failed); transient, and the next pass asks again.

    The last two are refusals with nothing behind them, and they are why this
    enum exists: ``DISABLED`` (the operator switched auto-replan off) and
    ``BUDGET_EXHAUSTED`` (the lineage is at ``auto_replan_max_generations``).
    Both mean no automatic route remains, and an initiative in that state
    needs a person rather than another pass.
    """

    SCHEDULED = "scheduled"
    ALREADY_RUNNING = "already_running"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    BUDGET_EXHAUSTED = "budget_exhausted"


#: Dispositions where something is happening or will happen without anyone
#: being asked. Their complement is what escalates, so a member added to the
#: enum and to neither set is a mypy error at the routing site rather than a
#: silent pass-through.
REPLAN_IN_PROGRESS_DISPOSITIONS: Final[frozenset[ReplanDisposition]] = frozenset(
    {
        ReplanDisposition.SCHEDULED,
        ReplanDisposition.ALREADY_RUNNING,
        ReplanDisposition.UNAVAILABLE,
    }
)


def _work_item_is_dead(item: ItemProgress) -> bool:
    """Whether an outstanding WORK item can no longer move on its own.

    Three task states are deliberately not dead, each because replanning would
    destroy something. Work still moving (CREATED through IN_REVIEW) is simply
    in flight. A human wait (AWAITING_INPUT, AUTH_REQUIRED) is the org waiting
    on the operator, and a replan would discard the question. An item whose
    task has not been persisted yet reads identically to one that will never
    dispatch, and dispatch writes the plan's EXECUTING status *before* it
    creates the task rows, so treating it as dead would replan every
    initiative during its own dispatch window.

    A fourth carve-out reads the reason rather than the status, because
    BLOCKED is reached from directions that mean opposite things and only the
    reason tells them apart. Which reasons is not decided here: every
    ``BlockedReason`` declares who ends its park, and a park a person or a
    sweep will end is the same shape of wait as AWAITING_INPUT, expressed
    through BLOCKED instead of its own status.

    Returns:
        ``True`` when the item's task is in a status nothing will move it out
        of without a new decision.
    """
    if item.blocked_reason in ATTENDED_BLOCKED_REASONS:
        return False
    return item.task_status in _DEAD_STATUSES


def _decision_item_is_dead(item: ItemProgress) -> bool:
    """Whether an undecided DECISION item can no longer be resolved by anyone.

    A DECISION item never has a task row, so the WORK rule's "no task row yet"
    carve-out never applies to it and asking it about ``task_status`` answered
    ``None`` forever: the plan could not be classified as stalled no matter
    what happened to it, so no replan could ever fire while the item itself
    was unanswerable. Both exits were shut.

    An undecided decision with options is a human wait, exactly like
    AWAITING_INPUT: the org is waiting on the operator and replanning would
    discard the question. With no options there is nothing for anyone to
    choose, so it is dead and the plan replans instead of hanging.

    Returns:
        ``True`` when the item is undecided and offers nothing to decide.
    """
    return not item.has_options


def _outstanding_is_dead(item: ItemProgress) -> bool:
    """Dispatch the dead test on the item's kind.

    Returns:
        ``True`` when this outstanding item cannot move on its own.
    """
    if item.kind is PlanItemKind.DECISION:
        return _decision_item_is_dead(item)
    return _work_item_is_dead(item)


def _is_failure_dead(item: ItemProgress) -> bool:
    """Whether a dead item was attempted and did not survive.

    An unanswerable decision was never attempted: nobody could act on it, so
    it reads as blocked rather than failed.

    Returns:
        ``True`` when the item's work was attempted and died.
    """
    if item.kind is PlanItemKind.DECISION:
        return False
    return item.task_status in _DEAD_BY_FAILURE


def stall_reason(items: tuple[ItemProgress, ...]) -> StallReason | None:
    """Classify whether a plan has run out of ways to advance.

    A plan is stalled when it has outstanding items and *none* of them can
    move without a new decision. That is a shape, not a duration, so there is
    no threshold to tune and no timer to run: the derivation is exact the
    moment the last live item dies.

    Returns:
        The reason, or ``None`` when the plan is done or can still advance.
    """
    outstanding = [item for item in items if not item_is_done(item)]
    if not outstanding:
        return None
    if not all(_outstanding_is_dead(item) for item in outstanding):
        return None
    failure_dead = [_is_failure_dead(item) for item in outstanding]
    if all(failure_dead):
        return StallReason.ALL_FAILED
    if not any(failure_dead):
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

    A plan in the head stage is left alone unconditionally. Its items exist but
    none has been dispatched, and the skeleton stage advances it on its own
    task's outcome, so deriving anything here would be a second owner for that
    hop. The guard is explicit rather than left to the fall-through, because
    the fall-through only holds while ``all_done`` is false: a plan whose items
    were somehow all already done would otherwise derive ``INTEGRATING`` and
    skip the contract entirely.

    Returns:
        ``INTEGRATING`` when a plan with items has every one done;
        ``EXECUTING`` when a tail-stage plan has an item no longer done;
        otherwise *current* unchanged (including for a head-stage plan and for
        a terminal one, which the rollup never reopens).
    """
    if current in TERMINAL_STATUSES or current in HEAD_STATUSES:
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
