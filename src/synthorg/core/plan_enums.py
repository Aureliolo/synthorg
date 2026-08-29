# module-kind: code
"""Plan lifecycle enumerations."""

from enum import StrEnum
from typing import Final


class PlanStatus(StrEnum):
    """Lifecycle status of a decomposed plan, from greenlight through delivery.

    A plan is PLANNING while it is a persisted-at-greenlight shell whose items
    the decomposer has not filled in yet, DRAFT while it is being shaped, and
    PENDING_REVIEW once it is parked for the operator's decision. APPROVED
    records the operator's yes. SKELETON is where the contract becomes code
    before any unit builds against it: module layout, one pending test per
    acceptance criterion, and the project's gate configuration, committed as
    one reviewable change. EXECUTING then covers the window where its items'
    tasks are in flight.

    APPROVED does not reach EXECUTING. A unit briefed in prose has no
    mechanical definition of done, so the units would be building against a
    contract that exists only in paragraphs; routing every dispatch through
    SKELETON is what makes the contract a signature and a failing test instead.

    Every item being done is the start of the tail, not the end of the plan.
    INTEGRATING is where the verified pieces are assembled into one running
    deliverable and checked end to end; EVALUATING is where that whole is
    scored against the objective's success criteria. Only then is COMPLETED
    reachable, and only from EVALUATING: a plan cannot jump from EXECUTING to
    COMPLETED. The guard that makes that true is the transition table in
    ``core/plan_transitions.py``, not this enum; a reader auditing the ordering
    should look there (and at ``scripts/check_verified_completion_paths.py``,
    which holds the forbidden edges out of the table). Either tail stage can
    fall back to EXECUTING when an item regresses (integration findings routed
    back as rework).

    REJECTED, SUPERSEDED, COMPLETED, and FAILED are terminal; an operator
    rework or request-changes is only accepted while the plan is still under
    review (see :data:`REWORKABLE_STATUSES`). FAILED marks a plan whose run
    could not be delivered at all: decomposition failed, parking the approval
    failed after it was filled, or an approved plan could not be dispatched. A
    failed run therefore always leaves a visible plan carrying its
    :attr:`Plan.failure_reason` rather than a silent orphan; a retry is a fresh
    plan. SUPERSEDED marks a plan retired by a re-plan, at any stage up to and
    including the tail.
    """

    PLANNING = "planning"
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    SKELETON = "skeleton"
    EXECUTING = "executing"
    INTEGRATING = "integrating"
    EVALUATING = "evaluating"
    COMPLETED = "completed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    FAILED = "failed"


#: Statuses from which an operator rework / request-changes is accepted. A
#: transient PLANNING shell (no items yet), a dispatched plan (APPROVED /
#: SKELETON / EXECUTING), and every terminal plan are excluded; edits are rejected
#: with a conflict. Reworking a dispatched plan is a re-plan, which supersedes
#: the current revision rather than editing it in place.
REWORKABLE_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {PlanStatus.DRAFT, PlanStatus.PENDING_REVIEW}
)

#: Terminal statuses: the plan has been delivered, declined, retired, or failed
#: to decompose, and has no remaining lifecycle hops. APPROVED is deliberately
#: absent: it opens SKELETON and is therefore mid-lifecycle.
TERMINAL_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {
        PlanStatus.COMPLETED,
        PlanStatus.REJECTED,
        PlanStatus.SUPERSEDED,
        PlanStatus.FAILED,
    }
)

#: The tail stages between "every item is done" and delivery: assembling the
#: verified pieces, then scoring the whole against the objective. Their own
#: gates advance them, so a derivation over plan items leaves them untouched.
TAIL_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {PlanStatus.INTEGRATING, PlanStatus.EVALUATING}
)

#: The head stage, before any item's task is in flight: the contract is written
#: as code and reviewed once, so the units below it build against signatures and
#: failing tests rather than paragraphs. Kept apart from :data:`TAIL_STATUSES`
#: rather than folded into it. The two share a mechanism (a stage keyed on a
#: derived task id, re-driven by one rollup pass) and nothing else: the tail set
#: also answers which statuses map to a finished project, which a re-plan may
#: retire, and which a delete route refuses, and none of those answers is the
#: same at the head. One frozenset carrying both would have to be wrong about
#: one of them.
HEAD_STATUSES: Final[frozenset[PlanStatus]] = frozenset({PlanStatus.SKELETON})

#: Every stage that owns its own advance: a derived task id it mints, reads back
#: and acts on, rather than a status a derivation over plan items computes. The
#: rollup re-drives any of these with a single recompute, which is what makes a
#: restart mid-stage resumable without minting a second job.
STAGE_STATUSES: Final[frozenset[PlanStatus]] = HEAD_STATUSES | TAIL_STATUSES

#: Statuses a re-plan accepts. A dispatched plan cannot be edited in place (its
#: items are already building), so revising it retires the current revision and
#: opens a successor. The tail stages are included: a failed integration or an
#: unmet success criterion is exactly the case a re-plan exists for. SKELETON is
#: included for the same reason and it is the cheapest of them: a contract that
#: will not compile is wrong about the plan, and catching that before any unit
#: has built against it is the whole point of writing the contract first. A plan
#: still under review is edited instead, and a terminal plan has nothing left
#: to revise.
REPLANNABLE_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {
        PlanStatus.APPROVED,
        PlanStatus.SKELETON,
        PlanStatus.EXECUTING,
        PlanStatus.INTEGRATING,
        PlanStatus.EVALUATING,
    }
)

#: Statuses a plan may be deleted from, stated as an allowlist rather than as
#: the complement of "dispatched". The route exists to clear a request that
#: never became work: a shell whose decomposition stranded, a draft, one still
#: waiting on review, or one that failed. Everything else is refused, and for
#: two different reasons. A dispatched plan is the record its running tasks
#: were approved against. A terminal one is the record of what was decided:
#: ``initiative_evaluation_report`` cascades off the plan row, so deleting a
#: COMPLETED plan destroys its delivery verdicts, and deleting a REJECTED or
#: SUPERSEDED one erases the decision not to build it.
DELETABLE_STATUSES: Final[frozenset[PlanStatus]] = frozenset(
    {
        PlanStatus.PLANNING,
        PlanStatus.DRAFT,
        PlanStatus.PENDING_REVIEW,
        PlanStatus.FAILED,
    }
)

#: Statuses whose plan may carry an empty item list: the PLANNING shell has not
#: been filled yet, and a FAILED plan may have failed before any items were
#: produced (which is also why a project teardown fails an itemless plan rather
#: than superseding it: SUPERSEDED demands items the plan never had). FAILED
#: permits (but does not require) empty items; every other status must carry a
#: non-empty, validated item DAG.
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
    """The kind of gap a plan-review finding flags.

    The vocabulary answers the questions the reviewer brief actually poses, so
    every question a panellist is told to ask has somewhere to put its answer.
    A narrower set does not make reviewers say less: it makes them propose a
    category the enum cannot express, get rejected, and resubmit under a worse
    one, at a turn per reviewer per panel.

    ``GAP`` is something the plan is missing. ``MISSING_OWNER`` is an item no
    accountable role owns. ``MISCALIBRATED_STAKES`` is a stakes level that does
    not match the work. ``RISKY_DECISION`` is a decision item whose options or
    recommendation do not hold up. ``BUDGET_CONCERN`` is cost. ``SEQUENCING``
    is a claim about the graph rather than the items: work ordered wrongly,
    serialised when it could run in parallel, or an item that cannot possibly
    precede what it depends on. ``UNVERIFIABLE_CRITERIA`` is an item whose
    definition of done cannot be checked. ``OVERSIZED_SCOPE`` is one item
    carrying what should be several.

    ``OTHER`` stays reachable, but a finding landing there is worth reading as
    a signal about this enum rather than as a routine outcome.
    """

    GAP = "gap"
    MISSING_OWNER = "missing_owner"
    MISCALIBRATED_STAKES = "miscalibrated_stakes"
    RISKY_DECISION = "risky_decision"
    BUDGET_CONCERN = "budget_concern"
    SEQUENCING = "sequencing"
    UNVERIFIABLE_CRITERIA = "unverifiable_criteria"
    OVERSIZED_SCOPE = "oversized_scope"
    OTHER = "other"
