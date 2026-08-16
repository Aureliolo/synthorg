# module-kind: code
"""Project lifecycle state machine transitions.

Defines the valid state transitions for an initiative::

    PLANNING -> ACTIVE | CANCELLED
    ACTIVE -> INTEGRATING | ON_HOLD | CANCELLED
    INTEGRATING -> EVALUATING | ACTIVE | ON_HOLD | CANCELLED
    EVALUATING -> COMPLETED | ACTIVE | ON_HOLD | CANCELLED
    ON_HOLD -> ACTIVE | CANCELLED

COMPLETED and CANCELLED are terminal.

An initiative reaches COMPLETED only from EVALUATING, mirroring its plan: work
that was individually verified has not been shown to assemble into a working
whole, so the integrate and evaluate stages sit between building and delivery
rather than being skippable. The back-edges to ACTIVE carry a regression,
where a tail stage sends items back for rework.

There is deliberately no failure status. Nothing downstream can honestly derive
that an initiative is dead: a completion-oracle REJECT routes a task back to
IN_PROGRESS for rework, and a task that does reach FAILED stays reassignable
(``FAILED -> ASSIGNED`` in :mod:`synthorg.core.task_transitions`), so a derived
failure would flap the moment the work was retried. Ending an initiative is a
human act (CANCELLED); failed and blocked work surfaces as derived counts on
the project's progress view rather than as a lifecycle state.

ON_HOLD has no direct hop to COMPLETED: an operator who paused an initiative
must resume it before the rollup can finish it, so work cannot complete out
from under a deliberate hold. Resuming returns to ACTIVE, from which the tail
is re-derived, rather than dropping the operator back into a half-finished
stage whose gate has already run.
"""

from typing import Final

from synthorg.core.project_enums import ProjectStatus
from synthorg.core.state_machine import HopRules, StateMachine
from synthorg.observability.events.project import (
    PROJECT_TRANSITION_CONFIG_ERROR,
    PROJECT_TRANSITION_INVALID,
)

VALID_TRANSITIONS: dict[ProjectStatus, frozenset[ProjectStatus]] = {
    ProjectStatus.PLANNING: frozenset({ProjectStatus.ACTIVE, ProjectStatus.CANCELLED}),
    ProjectStatus.ACTIVE: frozenset(
        {
            ProjectStatus.INTEGRATING,
            ProjectStatus.ON_HOLD,
            ProjectStatus.CANCELLED,
        }
    ),
    ProjectStatus.INTEGRATING: frozenset(
        {
            ProjectStatus.EVALUATING,
            ProjectStatus.ACTIVE,
            ProjectStatus.ON_HOLD,
            ProjectStatus.CANCELLED,
        }
    ),
    ProjectStatus.EVALUATING: frozenset(
        {
            ProjectStatus.COMPLETED,
            ProjectStatus.ACTIVE,
            ProjectStatus.ON_HOLD,
            ProjectStatus.CANCELLED,
        }
    ),
    ProjectStatus.ON_HOLD: frozenset({ProjectStatus.ACTIVE, ProjectStatus.CANCELLED}),
    ProjectStatus.COMPLETED: frozenset(),  # terminal
    ProjectStatus.CANCELLED: frozenset(),  # terminal
}

# No transition_event: the machine would log the transition INFO from
# validate(), before the row is written. The writer in
# ``engine/initiative/project_writes.py`` emits PROJECT_TRANSITION after each
# hop lands, so the audit trail records transitions that actually happened.
#: Cancellation is always available: it needs nothing the project may lack, so
#: no project status is a dead end an operator cannot leave.
_UNCONDITIONAL_TARGETS: Final[frozenset[ProjectStatus]] = frozenset(
    {ProjectStatus.CANCELLED}
)

_MACHINE: Final[StateMachine[ProjectStatus]] = StateMachine(
    VALID_TRANSITIONS,
    name="project_status",
    display_label="project status",
    invalid_event=PROJECT_TRANSITION_INVALID,
    config_event=PROJECT_TRANSITION_CONFIG_ERROR,
    all_states=ProjectStatus,
    hops=HopRules(unconditional_targets=_UNCONDITIONAL_TARGETS),
)


def validate_transition(current: ProjectStatus, target: ProjectStatus) -> None:
    """Validate that a project state transition is allowed.

    Args:
        current: The current project status.
        target: The desired target status.

    Raises:
        ValueError: If the transition from *current* to *target* is not in
            :data:`VALID_TRANSITIONS`.
    """
    _MACHINE.validate(current, target)


def transition_path(
    current: ProjectStatus,
    target: ProjectStatus,
) -> tuple[ProjectStatus, ...] | None:
    """Return the shortest valid hop sequence from *current* to *target*.

    Used by the rollup to advance a project that is several valid hops away
    from its derived status (e.g. PLANNING to COMPLETED, which now walks
    ACTIVE, INTEGRATING, and EVALUATING because delivery has exactly one
    predecessor).

    Args:
        current: The current project status.
        target: The desired project status.

    Returns:
        ``()`` when already at *target*; a tuple of intermediate statuses
        ending in *target* (each hop individually valid) when a lifecycle path
        exists; or ``None`` when *target* is unreachable from *current* (e.g.
        *current* is terminal).
    """
    return _MACHINE.path_to(current, target)
