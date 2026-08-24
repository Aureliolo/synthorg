# module-kind: code
"""Pure helpers for :class:`SprintService`.

Stateless functions and constants extracted from the service so the
orchestration module stays within its size budget. Each function takes
its inputs explicitly and returns a value; none touch persistence or the
scheduler.
"""

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final

from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, TaskStatus
from synthorg.engine.errors import SprintTransitionConflictError
from synthorg.engine.workflow.sprint_lifecycle import (
    VALID_SPRINT_TRANSITIONS,
    Sprint,
    SprintStatus,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import SPRINT_STATUS_TRANSITIONED

logger = get_logger(__name__)

# Story-point sizing per complexity (Fibonacci-ish). A fixed sizing
# convention rather than a user knob; the sprint velocity maths only needs
# a consistent per-task weight.
_COMPLEXITY_POINTS: Final[Mapping[Complexity, float]] = MappingProxyType(
    {
        Complexity.SIMPLE: 1.0,
        Complexity.MEDIUM: 3.0,
        Complexity.COMPLEX: 5.0,
        Complexity.EPIC: 8.0,
    }
)
_DEFAULT_STORY_POINTS: Final[float] = 1.0

# Statuses a task can no longer leave; excluded from a fresh sprint backlog.
TERMINAL_TASK_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
        TaskStatus.REJECTED,
        TaskStatus.FAILED,
    }
)
# Statuses worth seeding into a new sprint backlog, in enum order. Queried
# per-status so terminal tasks can never crowd out open ones under a row cap.
NON_TERMINAL_TASK_STATUSES: Final[tuple[TaskStatus, ...]] = tuple(
    status for status in TaskStatus if status not in TERMINAL_TASK_STATUSES
)


def log_sprint_transition(sprint: Sprint, previous: SprintStatus) -> None:
    """Emit the state-transition INFO log after a persisted hop.

    Lives here rather than on the service because the tail walk and the
    recovery sweep both persist hops, and a per-caller copy is how the
    same event comes to be logged with three different key sets.
    """
    logger.info(
        SPRINT_STATUS_TRANSITIONED,
        sprint_id=sprint.id,
        from_status=previous.value,
        to_status=sprint.status.value,
    )


def story_points_for(task: Task) -> float:
    """Map a task's complexity to a story-point weight.

    Returns:
        The per-task story-point weight for the sprint velocity maths.
    """
    return _COMPLEXITY_POINTS.get(task.estimated_complexity, _DEFAULT_STORY_POINTS)


def next_status(sprint: Sprint) -> SprintStatus:
    """Return the single legal next status, or raise when terminal.

    Returns:
        The one status reachable from the sprint's current status.

    Raises:
        SprintTransitionConflictError: When the sprint is terminal.
    """
    targets = VALID_SPRINT_TRANSITIONS[sprint.status]
    if not targets:
        msg = f"Sprint {sprint.id!r} is terminal ({sprint.status.value})"
        raise SprintTransitionConflictError(msg)
    return next(iter(targets))


def transition_overrides(
    sprint: Sprint, target: SprintStatus, *, now_iso: str
) -> dict[str, object]:
    """Date overrides stamped when advancing *sprint* to *target*.

    Returns:
        The ``start_date`` / ``end_date`` overrides for the hop.
    """
    overrides: dict[str, object] = {}
    if target is SprintStatus.ACTIVE and sprint.start_date is None:
        overrides["start_date"] = now_iso
    if target is SprintStatus.COMPLETED and sprint.end_date is None:
        overrides["end_date"] = now_iso
    return overrides


def open_backlog_tasks(candidates: Sequence[Task], *, cap: int) -> tuple[Task, ...]:
    """Filter *candidates* to non-terminal tasks, capped at *cap*.

    Returns:
        The open tasks to seed a new sprint backlog (at most ``cap``).
    """
    open_tasks = tuple(t for t in candidates if t.status not in TERMINAL_TASK_STATUSES)
    return open_tasks[:cap]


__all__ = [
    "NON_TERMINAL_TASK_STATUSES",
    "TERMINAL_TASK_STATUSES",
    "log_sprint_transition",
    "next_status",
    "open_backlog_tasks",
    "story_points_for",
    "transition_overrides",
]
