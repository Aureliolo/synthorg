# module-kind: code
"""Plan lifecycle state machine transitions.

Defines the valid state transitions for a durable plan::

    PLANNING -> DRAFT | PENDING_REVIEW | SUPERSEDED | FAILED
    DRAFT -> PENDING_REVIEW | SUPERSEDED | FAILED
    PENDING_REVIEW -> DRAFT | APPROVED | REJECTED | SUPERSEDED | FAILED
    APPROVED -> SKELETON | SUPERSEDED | FAILED
    SKELETON -> EXECUTING | SUPERSEDED | FAILED
    EXECUTING -> INTEGRATING | SUPERSEDED | FAILED
    INTEGRATING -> EVALUATING | EXECUTING | SUPERSEDED | FAILED
    EVALUATING -> COMPLETED | EXECUTING | SUPERSEDED | FAILED

COMPLETED, REJECTED, SUPERSEDED, and FAILED are terminal.

APPROVED is mid-lifecycle rather than terminal: the approval decision
dispatches the plan, and EXECUTING covers the window where its items' tasks are
in flight. Every item being done ends EXECUTING, not the plan: the work is then
INTEGRATING (assembled into one running deliverable and checked end to end) and
EVALUATING (scored against the objective's success criteria).

**EXECUTING is reachable only from SKELETON.** There is deliberately no
``APPROVED -> EXECUTING`` edge, and it is the same argument as the missing
``EXECUTING -> COMPLETED`` one hop later. A unit briefed in prose carries no
definition of done a machine can decide, so units dispatched straight off an
approved plan build against a contract that exists only in paragraphs and the
first thing that reconciles them is the assembly at the end. Routing every
dispatch through SKELETON makes the contract a signature plus a failing test
before anything is built on it, and makes that ordering the machine's rather
than whichever service happens to be wired.

**COMPLETED is reachable only from EVALUATING.** There is deliberately no
``EXECUTING -> COMPLETED`` edge: an initiative that delivered a pile of
individually-verified pieces has not been shown to deliver a working whole, so
the tail is enforced by the machine rather than by whichever service happens to
be wired. The back-edges to EXECUTING carry a regression (integration findings
routed back as rework) without a re-plan.

SUPERSEDED is reachable from every live status: a re-plan can retire a plan at
any stage, including from either tail stage, which is how a failed integration
or an unmet success criterion is resolved.

FAILED covers a run that could not be delivered at all: decomposition failed,
parking the approval failed, or an approved plan could not be dispatched. The
last is why APPROVED and EXECUTING reach it. Dispatch moves the plan to
EXECUTING before it builds the task tree, so that the rollup never observes a
project still PLANNING with tasks already running; a dispatch that then fails
leaves a plan EXECUTING with a failed parent and no children, which is a state
with no exit and nobody watching. The edge is what gets it out.

Both tail stages reach FAILED for the same reason: an assembly that will not
assemble, or a judgement that cannot run, with no replan routing it anywhere.
A replan (SUPERSEDED) resolves the tail failures somebody chooses to re-plan;
the ones nobody re-plans would otherwise sit in the tail with no exit, which
is the shape that made a whole project undeletable.
"""

from typing import Final

from synthorg.core.plan_enums import PlanStatus
from synthorg.core.state_machine import HopRules, StateMachine
from synthorg.observability.events.plan import (
    PLAN_TRANSITION_CONFIG_ERROR,
    PLAN_TRANSITION_INVALID,
)

VALID_TRANSITIONS: dict[PlanStatus, frozenset[PlanStatus]] = {
    # The greenlight shell is filled by the decomposer, which may hand back a
    # draft or park it straight for review.
    PlanStatus.PLANNING: frozenset(
        {
            PlanStatus.DRAFT,
            PlanStatus.PENDING_REVIEW,
            PlanStatus.SUPERSEDED,
            PlanStatus.FAILED,
        }
    ),
    PlanStatus.DRAFT: frozenset(
        {
            PlanStatus.PENDING_REVIEW,
            PlanStatus.SUPERSEDED,
            PlanStatus.FAILED,
        }
    ),
    PlanStatus.PENDING_REVIEW: frozenset(
        {
            PlanStatus.DRAFT,  # request-changes
            PlanStatus.APPROVED,
            PlanStatus.REJECTED,
            PlanStatus.SUPERSEDED,
            PlanStatus.FAILED,
        }
    ),
    PlanStatus.APPROVED: frozenset(
        {PlanStatus.SKELETON, PlanStatus.SUPERSEDED, PlanStatus.FAILED}
    ),
    # FAILED here is the head stage's own dead end: a contract that will not
    # compile, or a skeleton whose review never approved it. It is the cheapest
    # place in the lifecycle to fail, because nothing has been built against the
    # contract yet.
    PlanStatus.SKELETON: frozenset(
        {PlanStatus.EXECUTING, PlanStatus.SUPERSEDED, PlanStatus.FAILED}
    ),
    PlanStatus.EXECUTING: frozenset(
        {PlanStatus.INTEGRATING, PlanStatus.SUPERSEDED, PlanStatus.FAILED}
    ),
    # FAILED here is the tail's own dead end: an assembly that will not
    # assemble, or a judgement that cannot run, with no replan to route it.
    # Without it a plan that got as far as the tail and then stopped had no
    # exit at all, which is the same shape as an EXECUTING plan whose every
    # task failed.
    PlanStatus.INTEGRATING: frozenset(
        {
            PlanStatus.EVALUATING,
            PlanStatus.EXECUTING,
            PlanStatus.SUPERSEDED,
            PlanStatus.FAILED,
        }
    ),
    PlanStatus.EVALUATING: frozenset(
        {
            PlanStatus.COMPLETED,
            PlanStatus.EXECUTING,
            PlanStatus.SUPERSEDED,
            PlanStatus.FAILED,
        }
    ),
    PlanStatus.COMPLETED: frozenset(),  # terminal
    PlanStatus.REJECTED: frozenset(),  # terminal
    PlanStatus.SUPERSEDED: frozenset(),  # terminal
    PlanStatus.FAILED: frozenset(),  # terminal
}

#: Statuses any writer can reach with nothing but a reason it authors itself.
#: SUPERSEDED is absent: it demands a non-empty item DAG, which a plan still
#: being drafted does not have. COMPLETED is absent because only the evaluate
#: stage's verdict may write it.
_UNCONDITIONAL_TARGETS: Final[frozenset[PlanStatus]] = frozenset(
    {PlanStatus.FAILED, PlanStatus.REJECTED}
)

# No transition_event: validate() runs before the row is written, so the
# machine would record a transition that a failed write never made. PlanService
# emits its own status-transition INFO after the write succeeds.
_MACHINE: Final[StateMachine[PlanStatus]] = StateMachine(
    VALID_TRANSITIONS,
    name="plan_status",
    display_label="plan status",
    invalid_event=PLAN_TRANSITION_INVALID,
    config_event=PLAN_TRANSITION_CONFIG_ERROR,
    all_states=PlanStatus,
    hops=HopRules(unconditional_targets=_UNCONDITIONAL_TARGETS),
)


def validate_transition(current: PlanStatus, target: PlanStatus) -> None:
    """Validate that a plan state transition is allowed.

    Args:
        current: The current plan status.
        target: The desired target status.

    Raises:
        ValueError: If the transition from *current* to *target* is not in
            :data:`VALID_TRANSITIONS`.
    """
    _MACHINE.validate(current, target)


def transition_path(
    current: PlanStatus,
    target: PlanStatus,
) -> tuple[PlanStatus, ...] | None:
    """Return the shortest valid hop sequence from *current* to *target*.

    Used by the rollup to advance a plan that is several valid hops away from
    its derived status (e.g. APPROVED to INTEGRATING via SKELETON and EXECUTING,
    when a plan's dispatch-time status write lost its race). COMPLETED is never a
    target here: the evaluate stage writes it directly, as the single hop out
    of EVALUATING that its verdict earns.

    Args:
        current: The current plan status.
        target: The desired plan status.

    Returns:
        ``()`` when already at *target*; a tuple of intermediate statuses
        ending in *target* (each hop individually valid) when a lifecycle path
        exists; or ``None`` when *target* is unreachable from *current* (e.g.
        *current* is terminal) or is COMPLETED (delivery is the evaluate
        stage's verdict, never a walked hop).
    """
    if target is PlanStatus.COMPLETED:
        return None
    return _MACHINE.path_to(current, target)
