# module-kind: code
"""Plan lifecycle state machine transitions.

Defines the valid state transitions for a durable plan::

    PLANNING -> DRAFT | PENDING_REVIEW | SUPERSEDED | FAILED
    DRAFT -> PENDING_REVIEW | SUPERSEDED | FAILED
    PENDING_REVIEW -> DRAFT | APPROVED | REJECTED | SUPERSEDED | FAILED
    APPROVED -> EXECUTING | SUPERSEDED
    EXECUTING -> COMPLETED | SUPERSEDED

COMPLETED, REJECTED, SUPERSEDED, and FAILED are terminal.

APPROVED is mid-lifecycle rather than terminal: the approval decision
dispatches the plan, and EXECUTING covers the window where its items' tasks are
in flight. COMPLETED is reached only once every item is genuinely done, which
composes with the verify gate because a WORK item's task can only reach
COMPLETED through the review gate's oracle chain.

SUPERSEDED is reachable from every live status: a re-plan can retire a plan at
any stage, including mid-execution. FAILED covers a run that never reached a
review decision (decomposition failed, or parking the approval failed), so it
is reachable only from the pre-decision statuses.
"""

from typing import Final

from synthorg.core.plan_enums import PlanStatus
from synthorg.core.state_machine import StateMachine
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
    PlanStatus.APPROVED: frozenset({PlanStatus.EXECUTING, PlanStatus.SUPERSEDED}),
    PlanStatus.EXECUTING: frozenset({PlanStatus.COMPLETED, PlanStatus.SUPERSEDED}),
    PlanStatus.COMPLETED: frozenset(),  # terminal
    PlanStatus.REJECTED: frozenset(),  # terminal
    PlanStatus.SUPERSEDED: frozenset(),  # terminal
    PlanStatus.FAILED: frozenset(),  # terminal
}

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
    its derived status (e.g. APPROVED to COMPLETED via EXECUTING).

    Args:
        current: The current plan status.
        target: The desired plan status.

    Returns:
        ``()`` when already at *target*; a tuple of intermediate statuses
        ending in *target* (each hop individually valid) when a lifecycle path
        exists; or ``None`` when *target* is unreachable from *current* (e.g.
        *current* is terminal).
    """
    return _MACHINE.path_to(current, target)
