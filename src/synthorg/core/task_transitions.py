"""Task lifecycle state machine transitions.

Defines the valid state transitions for the task lifecycle, based on
the Engine design page, extended with BLOCKED, CANCELLED,
FAILED, and INTERRUPTED transitions for completeness::

    CREATED -> ASSIGNED
    ASSIGNED -> IN_PROGRESS | BLOCKED | CANCELLED | FAILED | INTERRUPTED
    IN_PROGRESS -> IN_REVIEW | BLOCKED | CANCELLED | FAILED | INTERRUPTED
    IN_REVIEW -> COMPLETED | IN_PROGRESS (rework) | BLOCKED | CANCELLED
    BLOCKED -> ASSIGNED (unblocked)
    FAILED -> ASSIGNED (reassignment for retry)
    INTERRUPTED -> ASSIGNED (reassignment on restart)

COMPLETED and CANCELLED are terminal states with no outgoing
transitions.  FAILED and INTERRUPTED are non-terminal (can be reassigned).
"""

from synthorg.core.enums import TaskStatus
from synthorg.observability import get_logger
from synthorg.observability.events.task import (
    TASK_TRANSITION_CONFIG_ERROR,
    TASK_TRANSITION_INVALID,
)

logger = get_logger(__name__)

VALID_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset({TaskStatus.ASSIGNED}),
    TaskStatus.ASSIGNED: frozenset(
        {
            TaskStatus.IN_PROGRESS,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
            TaskStatus.INTERRUPTED,
        }
    ),
    TaskStatus.IN_PROGRESS: frozenset(
        {
            TaskStatus.IN_REVIEW,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
            TaskStatus.INTERRUPTED,
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
    TaskStatus.BLOCKED: frozenset({TaskStatus.ASSIGNED}),
    TaskStatus.FAILED: frozenset({TaskStatus.ASSIGNED}),  # reassignment
    TaskStatus.INTERRUPTED: frozenset({TaskStatus.ASSIGNED}),  # reassignment on restart
    TaskStatus.COMPLETED: frozenset(),  # terminal
    TaskStatus.CANCELLED: frozenset(),  # terminal
}

_missing = set(TaskStatus) - set(VALID_TRANSITIONS)
if _missing:
    _msg = f"Missing transition entries for: {sorted(s.value for s in _missing)}"
    raise ValueError(_msg)


def validate_transition(current: TaskStatus, target: TaskStatus) -> None:
    """Validate that a state transition is allowed.

    Args:
        current: The current task status.
        target: The desired target status.

    Raises:
        ValueError: If the transition from *current* to *target*
            is not in :data:`VALID_TRANSITIONS`.
    """
    if current not in VALID_TRANSITIONS:
        logger.critical(
            TASK_TRANSITION_CONFIG_ERROR,
            current_status=current.value,
        )
        msg = (
            f"TaskStatus {current.value!r} has no entry in VALID_TRANSITIONS. "
            f"This is a configuration error — update task_transitions.py."
        )
        raise ValueError(msg)
    allowed = VALID_TRANSITIONS[current]
    if target not in allowed:
        logger.warning(
            TASK_TRANSITION_INVALID,
            current_status=current.value,
            target_status=target.value,
            allowed=sorted(s.value for s in allowed),
        )
        msg = (
            f"Invalid task status transition: {current.value!r} -> "
            f"{target.value!r}. Allowed from {current.value!r}: "
            f"{sorted(s.value for s in allowed)}"
        )
        raise ValueError(msg)
