"""Task lifecycle state machine transitions.

Defines the valid state transitions for the task lifecycle, based on
the Engine design page, extended with BLOCKED, CANCELLED,
FAILED, INTERRUPTED, SUSPENDED, REJECTED, AUTH_REQUIRED, and
AWAITING_INPUT transitions for completeness::

    CREATED -> ASSIGNED | REJECTED | FAILED
    ASSIGNED -> IN_PROGRESS | AUTH_REQUIRED | BLOCKED | CANCELLED
               | FAILED | INTERRUPTED | SUSPENDED
    IN_PROGRESS -> IN_REVIEW | AWAITING_INPUT | AUTH_REQUIRED | BLOCKED
                   | CANCELLED | FAILED | INTERRUPTED | SUSPENDED
    IN_REVIEW -> COMPLETED | IN_PROGRESS (rework) | BLOCKED | CANCELLED
    AUTH_REQUIRED -> ASSIGNED (approved) | CANCELLED (denied/timeout)
    AWAITING_INPUT -> IN_PROGRESS (answer received) | CANCELLED
    BLOCKED -> ASSIGNED (unblocked) | IN_REVIEW (an escalated review's
               answer rejoins it) | CANCELLED (abandoned)
    FAILED -> ASSIGNED (reassignment for retry) | CANCELLED (abandoned)
    INTERRUPTED -> ASSIGNED (reassignment on restart) | CANCELLED (abandoned)
    SUSPENDED -> ASSIGNED (resume from checkpoint) | CANCELLED (abandoned)

COMPLETED, CANCELLED, and REJECTED are terminal states with no
outgoing transitions.  FAILED, INTERRUPTED, and SUSPENDED are
non-terminal (can be reassigned).  AUTH_REQUIRED (waiting for
authorization) and AWAITING_INPUT (paused for a human's answer to a
mid-task clarification) are non-terminal.

Every stuck state carries a direct ``CANCELLED`` exit because
reassignment is not one: ``ASSIGNED`` requires an assignee, and a task
that failed before it was ever assigned has none, so routing its
abandonment through ``ASSIGNED`` failed the ``Task`` validator and left
the row with no reachable exit at all. Its project then could not be
deleted by any route, because the cascade could not resolve it.
"""

from typing import Final

from synthorg.core.state_machine import HopRules, StateMachine
from synthorg.core.task_enums import TaskStatus
from synthorg.observability.events.task import (
    TASK_TRANSITION,
    TASK_TRANSITION_CONFIG_ERROR,
    TASK_TRANSITION_INVALID,
)

VALID_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    # CREATED -> FAILED: a greenlit objective's root task can fail during the
    # planning phase (decomposition never produced a plan) before it is ever
    # assigned; FAILED is re-runnable (-> ASSIGNED), so the failed run stays on
    # the board and re-runnable rather than becoming a silent orphan.
    # CREATED -> BLOCKED: routing found nobody the subtask could go to, so
    # there is no assignee to give it and nothing downstream will ever look at
    # it again. Parking names the condition on the row where an operator can
    # see it, rather than leaving the subtask CREATED on a board that reports
    # its plan as executing.
    TaskStatus.CREATED: frozenset(
        {
            TaskStatus.ASSIGNED,
            TaskStatus.BLOCKED,
            TaskStatus.REJECTED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.ASSIGNED: frozenset(
        {
            TaskStatus.IN_PROGRESS,
            TaskStatus.AUTH_REQUIRED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
            TaskStatus.INTERRUPTED,
            TaskStatus.SUSPENDED,
        }
    ),
    TaskStatus.IN_PROGRESS: frozenset(
        {
            TaskStatus.IN_REVIEW,
            TaskStatus.AWAITING_INPUT,
            TaskStatus.AUTH_REQUIRED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
            TaskStatus.INTERRUPTED,
            TaskStatus.SUSPENDED,
        }
    ),
    TaskStatus.IN_REVIEW: frozenset(
        {
            TaskStatus.COMPLETED,
            TaskStatus.IN_PROGRESS,  # rework
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        }
    ),
    # Mid-execution clarification pause: the agent asked a question and waits
    # for a human answer, then resumes (-> IN_PROGRESS) or is abandoned.
    TaskStatus.AWAITING_INPUT: frozenset(
        {TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED}
    ),
    TaskStatus.AUTH_REQUIRED: frozenset({TaskStatus.ASSIGNED, TaskStatus.CANCELLED}),
    # IN_REVIEW is here because a completion review that escalates parks the
    # task HERE for a human, and BLOCKED is therefore a state *inside* that
    # review rather than a detour around it. Without the edge back, the
    # escalation asks a question whose every answer is an illegal transition:
    # a task holding a decided approval and a verified build can reach only
    # ASSIGNED, which needs an assignee it may never have had, or CANCELLED.
    # Deliberately NOT a direct edge to COMPLETED: the human's answer rejoins
    # the review it came from, so COMPLETED stays reachable only through
    # IN_REVIEW and the completion oracle keeps its one chokepoint.
    TaskStatus.BLOCKED: frozenset(
        {TaskStatus.ASSIGNED, TaskStatus.CANCELLED, TaskStatus.IN_REVIEW}
    ),
    TaskStatus.FAILED: frozenset({TaskStatus.ASSIGNED, TaskStatus.CANCELLED}),
    TaskStatus.INTERRUPTED: frozenset({TaskStatus.ASSIGNED, TaskStatus.CANCELLED}),
    TaskStatus.SUSPENDED: frozenset({TaskStatus.ASSIGNED, TaskStatus.CANCELLED}),
    TaskStatus.COMPLETED: frozenset(),  # terminal
    TaskStatus.CANCELLED: frozenset(),  # terminal
    TaskStatus.REJECTED: frozenset(),  # terminal
}

#: Statuses any writer can move a task into with nothing but the task itself.
#: ``ASSIGNED`` is absent on purpose: it needs an assignee the task may not
#: have, which is what made abandonment unreachable from the stuck states.
_UNCONDITIONAL_TARGETS: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.CANCELLED, TaskStatus.REJECTED, TaskStatus.FAILED}
)

#: Parks a walk may land on or start from, and must never pass through.
#: Each means "something outside the task must change before it moves", and
#: each is read alongside a ``blocked_reason`` / an approval that a walker
#: driving the task somewhere else never writes. A rollup advancing a parent
#: to its derived status through one of these would record a wait that never
#: happened and hand the next reader a park with no reason on it.
_NO_TRANSIT: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.BLOCKED,
        TaskStatus.AWAITING_INPUT,
        TaskStatus.AUTH_REQUIRED,
        TaskStatus.SUSPENDED,
        TaskStatus.INTERRUPTED,
    }
)

_MACHINE: Final[StateMachine[TaskStatus]] = StateMachine(
    VALID_TRANSITIONS,
    name="task_status",
    display_label="task status",
    invalid_event=TASK_TRANSITION_INVALID,
    config_event=TASK_TRANSITION_CONFIG_ERROR,
    transition_event=TASK_TRANSITION,
    all_states=TaskStatus,
    hops=HopRules(
        unconditional_targets=_UNCONDITIONAL_TARGETS,
        no_transit_states=_NO_TRANSIT,
    ),
)


def validate_transition(current: TaskStatus, target: TaskStatus) -> None:
    """Validate that a state transition is allowed.

    Args:
        current: The current task status.
        target: The desired target status.

    Raises:
        ValueError: If the transition from *current* to *target*
            is not in :data:`VALID_TRANSITIONS`.
    """
    _MACHINE.validate(current, target)


def transition_path(
    current: TaskStatus,
    target: TaskStatus,
) -> tuple[TaskStatus, ...] | None:
    """Return the shortest valid hop sequence from *current* to *target*.

    Args:
        current: The current task status.
        target: The desired task status.

    Returns:
        ``()`` when already at *target*; a tuple of intermediate
        statuses ending in *target* (each hop individually valid) when
        a lifecycle path exists; or ``None`` when *target* is
        unreachable from *current* (e.g. *current* is terminal).
    """
    return _MACHINE.path_to(current, target)
