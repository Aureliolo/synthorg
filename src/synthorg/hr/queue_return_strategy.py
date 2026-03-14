"""Queue-return task reassignment strategy (D9 initial).

Transitions active tasks to INTERRUPTED so they can be re-assigned
to another agent by the task routing system.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import TaskStatus
from synthorg.hr.errors import TaskReassignmentError
from synthorg.observability import get_logger
from synthorg.observability.events.hr import (
    HR_FIRING_REASSIGNMENT_FAILED,
    HR_FIRING_TASKS_REASSIGNED,
)

if TYPE_CHECKING:
    from synthorg.core.task import Task
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

# Task statuses that should be interrupted during offboarding.
_ACTIVE_STATUSES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
    }
)


class QueueReturnStrategy:
    """Reassign tasks by returning them to the queue as INTERRUPTED.

    For tasks in ASSIGNED or IN_PROGRESS status, transitions them
    to INTERRUPTED via ``task.with_transition()``. Tasks already
    INTERRUPTED or in other states are skipped.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "queue_return"

    async def reassign(
        self,
        *,
        agent_id: NotBlankStr,
        active_tasks: tuple[Task, ...],
    ) -> tuple[Task, ...]:
        """Transition active tasks to INTERRUPTED.

        Args:
            agent_id: Agent being terminated.
            active_tasks: Tasks currently assigned to the agent.

        Returns:
            Tasks that were transitioned to INTERRUPTED.

        Raises:
            TaskReassignmentError: If a transition fails.
        """
        interrupted: list[Task] = []
        for task in active_tasks:
            if task.status not in _ACTIVE_STATUSES:
                continue
            try:
                updated = task.with_transition(
                    TaskStatus.INTERRUPTED,
                    assigned_to=None,
                )
                interrupted.append(updated)
            except ValueError as exc:
                msg = (
                    f"Failed to interrupt task {task.id!r} "
                    f"(status={task.status.value}): {exc}"
                )
                logger.warning(
                    HR_FIRING_REASSIGNMENT_FAILED,
                    agent_id=agent_id,
                    task_id=task.id,
                    error=msg,
                )
                raise TaskReassignmentError(msg) from exc

        logger.info(
            HR_FIRING_TASKS_REASSIGNED,
            agent_id=agent_id,
            count=len(interrupted),
        )
        return tuple(interrupted)
