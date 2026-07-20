# module-kind: code
"""Plan lifecycle enumerations."""

from enum import StrEnum
from typing import Final


class PlanStatus(StrEnum):
    """Lifecycle status of a decomposed plan, from greenlight through delivery.

    A plan is PLANNING while it is a persisted-at-greenlight shell whose items
    the decomposer has not filled in yet, DRAFT while it is being shaped, and
    PENDING_REVIEW once it is parked for the operator's decision. APPROVED
    records the operator's yes and dispatches the plan; EXECUTING covers the
    window where its items' tasks are in flight; COMPLETED is reached only once
    every item is genuinely done (a WORK item's task has passed the review
    gate, a DECISION item has a chosen option), so completion composes with the
    verify gate rather than restating it.

    REJECTED, SUPERSEDED, COMPLETED, and FAILED are terminal; an operator
    rework or request-changes is only accepted while the plan is still under
    review (see :data:`REWORKABLE_STATUSES`). FAILED marks a plan whose run
    failed to reach review (decomposition failed, or parking the approval
    failed after it was filled), so a failed run always leaves a visible plan
    carrying its :attr:`Plan.failure_reason` rather than a silent orphan; a
    retry is a fresh plan. SUPERSEDED marks a plan retired by a re-plan, at any
    stage up to and including execution.
    """

    PLANNING = "planning"
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    FAILED = "failed"


#: Statuses from which an operator rework / request-changes is accepted. A
#: transient PLANNING shell (no items yet), a dispatched plan (APPROVED /
#: EXECUTING), and every terminal plan are excluded; edits on them are rejected
#: with a conflict. Reworking a dispatched plan is a re-plan, which supersedes
#: the current revision rather than editing it in place.
REWORKABLE_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {PlanStatus.DRAFT, PlanStatus.PENDING_REVIEW}
)

#: Terminal statuses: the plan has been delivered, declined, retired, or failed
#: to decompose, and has no remaining lifecycle hops. APPROVED is deliberately
#: absent: it dispatches into EXECUTING and is therefore mid-lifecycle.
TERMINAL_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {
        PlanStatus.COMPLETED,
        PlanStatus.REJECTED,
        PlanStatus.SUPERSEDED,
        PlanStatus.FAILED,
    }
)

#: Statuses covering a dispatched plan whose work is in flight or delivered.
#: A project is ACTIVE while its plan is here, and COMPLETED once the plan is.
EXECUTION_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {PlanStatus.EXECUTING, PlanStatus.COMPLETED}
)

#: Statuses a re-plan accepts. A dispatched plan cannot be edited in place (its
#: items are already building), so revising it retires the current revision and
#: opens a successor. A plan still under review is edited instead, and a
#: terminal plan has nothing left to revise.
REPLANNABLE_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {PlanStatus.APPROVED, PlanStatus.EXECUTING}
)

#: Statuses whose plan may carry an empty item list: the PLANNING shell has not
#: been filled yet, and a FAILED plan may have failed before any items were
#: produced. FAILED permits (but does not require) empty items; every other
#: status must carry a non-empty, validated item DAG.
ITEMLESS_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {PlanStatus.PLANNING, PlanStatus.FAILED}
)


class PlanItemKind(StrEnum):
    """What a plan item represents.

    A ``WORK`` item is a unit of work a team executes. A ``DECISION`` item is a
    choice the plan surfaces for a reviewer: it carries options rather than
    dispatching a build task, and it is "done" once the decision is recorded.
    """

    WORK = "work"
    DECISION = "decision"


class PlanReviewVerdict(StrEnum):
    """A reviewer's (or the panel's synthesised) verdict on a plan.

    ``ENDORSED`` means the reviewer backs the plan as-is. ``CONCERNS`` means the
    reviewer raised findings the owner should address. ``REVISION_REQUESTED``
    sends the plan back to the owner to revise; a panellist may pick it
    directly, and it is also the synthesised overall verdict when any panellist
    requests one.
    """

    ENDORSED = "endorsed"
    CONCERNS = "concerns"
    REVISION_REQUESTED = "revision_requested"


class PlanReviewFindingCategory(StrEnum):
    """The kind of gap a plan-review finding flags."""

    GAP = "gap"
    MISSING_OWNER = "missing_owner"
    MISCALIBRATED_STAKES = "miscalibrated_stakes"
    RISKY_DECISION = "risky_decision"
    BUDGET_CONCERN = "budget_concern"
    OTHER = "other"
