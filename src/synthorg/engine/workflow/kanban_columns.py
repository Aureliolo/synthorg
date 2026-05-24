"""Kanban board column definitions, transitions, and task status bridge.

Defines the five Kanban columns from the Engine design page and their
relationship to the task lifecycle state machine.  Column transitions
are validated independently of (and mapped onto) task status transitions.
"""

from enum import StrEnum
from types import MappingProxyType
from typing import Final

from synthorg.core.enums import TaskStatus
from synthorg.core.state_machine import StateMachine
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    KANBAN_COLUMN_TRANSITION,
    KANBAN_COLUMN_TRANSITION_CONFIG_ERROR,
    KANBAN_COLUMN_TRANSITION_INVALID,
    KANBAN_STATUS_PATH_MISSING,
)

logger = get_logger(__name__)


class KanbanColumn(StrEnum):
    """Kanban board columns matching the Engine design page.

    Members:
        BACKLOG: Tasks waiting to be prioritized.
        READY: Prioritized and ready for assignment.
        IN_PROGRESS: Actively being worked on.
        REVIEW: Work complete, awaiting review.
        DONE: Finished and accepted.
    """

    BACKLOG = "backlog"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"


# -- Column <-> TaskStatus bridge -------------------------------------------

COLUMN_TO_STATUSES: MappingProxyType[KanbanColumn, frozenset[TaskStatus]] = (
    MappingProxyType(
        {
            KanbanColumn.BACKLOG: frozenset({TaskStatus.CREATED}),
            KanbanColumn.READY: frozenset({TaskStatus.ASSIGNED}),
            KanbanColumn.IN_PROGRESS: frozenset({TaskStatus.IN_PROGRESS}),
            KanbanColumn.REVIEW: frozenset({TaskStatus.IN_REVIEW}),
            KanbanColumn.DONE: frozenset({TaskStatus.COMPLETED}),
        }
    )
)

# Off-board statuses (BLOCKED, FAILED, INTERRUPTED, SUSPENDED, CANCELLED,
# REJECTED, AUTH_REQUIRED) map to None -- temporarily or permanently
# removed from the board.
STATUS_TO_COLUMN: MappingProxyType[TaskStatus, KanbanColumn | None] = MappingProxyType(
    {
        TaskStatus.CREATED: KanbanColumn.BACKLOG,
        TaskStatus.ASSIGNED: KanbanColumn.READY,
        TaskStatus.IN_PROGRESS: KanbanColumn.IN_PROGRESS,
        TaskStatus.IN_REVIEW: KanbanColumn.REVIEW,
        TaskStatus.COMPLETED: KanbanColumn.DONE,
        TaskStatus.BLOCKED: None,
        TaskStatus.FAILED: None,
        TaskStatus.INTERRUPTED: None,
        TaskStatus.SUSPENDED: None,
        TaskStatus.CANCELLED: None,
        TaskStatus.REJECTED: None,
        TaskStatus.AUTH_REQUIRED: None,
    }
)

# -- Module-level guards ----------------------------------------------------

_missing_columns = set(KanbanColumn) - set(COLUMN_TO_STATUSES)
if _missing_columns:
    _msg = (
        f"Missing COLUMN_TO_STATUSES entries for: "
        f"{sorted(c.value for c in _missing_columns)}"
    )
    raise ValueError(_msg)

_missing_statuses = set(TaskStatus) - set(STATUS_TO_COLUMN)
if _missing_statuses:
    _msg = (
        f"Missing STATUS_TO_COLUMN entries for: "
        f"{sorted(s.value for s in _missing_statuses)}"
    )
    raise ValueError(_msg)


def _validate_status_column_consistency() -> None:
    """Verify on-board STATUS_TO_COLUMN entries match COLUMN_TO_STATUSES."""
    for status, column in STATUS_TO_COLUMN.items():
        if column is not None and status not in COLUMN_TO_STATUSES[column]:
            msg = (
                f"STATUS_TO_COLUMN maps {status.value!r} to "
                f"{column.value!r}, but COLUMN_TO_STATUSES[{column.value!r}] "
                f"does not include {status.value!r}"
            )
            raise ValueError(msg)


_validate_status_column_consistency()

del _missing_columns, _missing_statuses


# -- Column transitions -----------------------------------------------------

VALID_COLUMN_TRANSITIONS: MappingProxyType[KanbanColumn, frozenset[KanbanColumn]] = (
    MappingProxyType(
        {
            KanbanColumn.BACKLOG: frozenset({KanbanColumn.READY, KanbanColumn.DONE}),
            KanbanColumn.READY: frozenset(
                {KanbanColumn.IN_PROGRESS, KanbanColumn.BACKLOG}
            ),
            KanbanColumn.IN_PROGRESS: frozenset(
                {
                    KanbanColumn.REVIEW,
                    KanbanColumn.BACKLOG,
                    KanbanColumn.READY,
                }
            ),
            KanbanColumn.REVIEW: frozenset(
                {KanbanColumn.DONE, KanbanColumn.IN_PROGRESS}
            ),
            KanbanColumn.DONE: frozenset(),  # terminal
        }
    )
)

_COLUMN_MACHINE: Final[StateMachine[KanbanColumn]] = StateMachine(
    VALID_COLUMN_TRANSITIONS,
    name="kanban_column",
    display_label="Kanban column",
    invalid_event=KANBAN_COLUMN_TRANSITION_INVALID,
    config_event=KANBAN_COLUMN_TRANSITION_CONFIG_ERROR,
    transition_event=KANBAN_COLUMN_TRANSITION,
    all_states=KanbanColumn,
)


# -- Task status transition paths per column move ---------------------------
# Maps (from_column, to_column) to the sequence of TaskStatus values
# the task must pass through.  Multi-step when columns are not adjacent
# in the task state machine (e.g. BACKLOG->DONE skips intermediate
# statuses).
#
# NOTE: Backward moves go through BLOCKED because the task state machine
# has no direct backward path.  Moves to READY end at ASSIGNED (on-board
# in the READY column).  Moves to BACKLOG end at BLOCKED (off-board)
# because there is no valid task transition from BLOCKED to CREATED; the
# task cannot actually land in BACKLOG through the status path alone.
# These backward-to-BACKLOG transitions are still modeled so the Kanban
# layer can express the intent -- the engine must handle the off-board
# result (e.g. reset via a dedicated mechanism outside the state machine).

_COLUMN_MOVE_STATUS_PATH: MappingProxyType[
    tuple[KanbanColumn, KanbanColumn], tuple[TaskStatus, ...]
] = MappingProxyType(
    {
        # BACKLOG -> ...
        (KanbanColumn.BACKLOG, KanbanColumn.READY): (TaskStatus.ASSIGNED,),
        (KanbanColumn.BACKLOG, KanbanColumn.DONE): (
            TaskStatus.ASSIGNED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.IN_REVIEW,
            TaskStatus.COMPLETED,
        ),
        # READY -> ...
        (KanbanColumn.READY, KanbanColumn.IN_PROGRESS): (TaskStatus.IN_PROGRESS,),
        (KanbanColumn.READY, KanbanColumn.BACKLOG): (
            TaskStatus.BLOCKED,
            TaskStatus.ASSIGNED,
            TaskStatus.BLOCKED,
        ),
        # IN_PROGRESS -> ...
        (KanbanColumn.IN_PROGRESS, KanbanColumn.REVIEW): (TaskStatus.IN_REVIEW,),
        (KanbanColumn.IN_PROGRESS, KanbanColumn.BACKLOG): (
            TaskStatus.BLOCKED,
            TaskStatus.ASSIGNED,
            TaskStatus.BLOCKED,
        ),
        (KanbanColumn.IN_PROGRESS, KanbanColumn.READY): (
            TaskStatus.BLOCKED,
            TaskStatus.ASSIGNED,
        ),
        # REVIEW -> ...
        (KanbanColumn.REVIEW, KanbanColumn.DONE): (TaskStatus.COMPLETED,),
        (KanbanColumn.REVIEW, KanbanColumn.IN_PROGRESS): (TaskStatus.IN_PROGRESS,),
    }
)


def _validate_status_path_coverage() -> None:
    """Verify every valid column transition has a status-path entry."""
    for from_col, targets in VALID_COLUMN_TRANSITIONS.items():
        for to_col in targets:
            if (from_col, to_col) not in _COLUMN_MOVE_STATUS_PATH:
                msg = (
                    f"Missing _COLUMN_MOVE_STATUS_PATH entry for "
                    f"{from_col.value!r} -> {to_col.value!r}"
                )
                raise ValueError(msg)


_validate_status_path_coverage()


def validate_column_transition(
    current: KanbanColumn,
    target: KanbanColumn,
) -> None:
    """Validate that a Kanban column transition is allowed.

    The success INFO log (``KANBAN_COLUMN_TRANSITION``) is emitted by
    :meth:`StateMachine.validate` via its ``transition_event``
    wiring; this thin wrapper just forwards the call so callers
    don't need to know about the underlying machine.

    Args:
        current: The current column.
        target: The desired target column.

    Raises:
        ValueError: If the transition is not allowed.
    """
    _COLUMN_MACHINE.validate(current, target)


def resolve_task_transitions(
    from_column: KanbanColumn,
    to_column: KanbanColumn,
) -> tuple[TaskStatus, ...]:
    """Return the TaskStatus path for a Kanban column move.

    The caller must apply these transitions sequentially to the task
    via the TaskEngine.  Does NOT validate the column transition itself;
    call :func:`validate_column_transition` first.

    Args:
        from_column: Source column.
        to_column: Target column.

    Returns:
        Ordered tuple of TaskStatus values the task must pass through.

    Raises:
        ValueError: If no status path is defined for this column pair.
    """
    key = (from_column, to_column)
    path = _COLUMN_MOVE_STATUS_PATH.get(key)
    if path is None:
        logger.warning(
            KANBAN_STATUS_PATH_MISSING,
            from_column=from_column.value,
            to_column=to_column.value,
        )
        msg = (
            f"No task status path defined for column move "
            f"{from_column.value!r} -> {to_column.value!r}"
        )
        raise ValueError(msg)
    return path
