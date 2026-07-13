# module-kind: code
"""Plan lifecycle enumerations."""

from enum import StrEnum
from typing import Final


class PlanStatus(StrEnum):
    """Lifecycle status of a decomposed plan through CEO review.

    A plan is DRAFT while it is being shaped and PENDING_REVIEW once it is
    parked for the operator's decision. APPROVED, REJECTED, and SUPERSEDED are
    terminal: an operator rework or a request-changes is only accepted from a
    non-terminal status (see :data:`REWORKABLE_STATUSES`). SUPERSEDED is
    reserved for a plan retired by a fresh re-plan; the current edit path
    revises a plan in place (bumping :attr:`Plan.version`) rather than
    retaining prior revisions.
    """

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


#: Statuses from which an operator rework / request-changes is accepted. A
#: terminal plan (APPROVED / REJECTED / SUPERSEDED) cannot be reworked back
#: into an active status; edits on it are rejected with a conflict.
REWORKABLE_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {PlanStatus.DRAFT, PlanStatus.PENDING_REVIEW}
)

#: Terminal statuses: a decision has been recorded and the plan is closed to
#: operator rework.
TERMINAL_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {PlanStatus.APPROVED, PlanStatus.REJECTED, PlanStatus.SUPERSEDED}
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
