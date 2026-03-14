"""Task reassignment strategy protocol.

Defines the interface for pluggable strategies that handle
task reassignment when an agent is being terminated (D9).
"""

from typing import Protocol, runtime_checkable

from synthorg.core.task import Task  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


@runtime_checkable
class TaskReassignmentStrategy(Protocol):
    """Strategy for reassigning tasks from a departing agent.

    Implementations determine how active tasks are handled when
    an agent is terminated — e.g. returned to queue, reassigned
    to a specific agent, or cancelled.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    async def reassign(
        self,
        *,
        agent_id: NotBlankStr,
        active_tasks: tuple[Task, ...],
    ) -> tuple[Task, ...]:
        """Reassign active tasks from a departing agent.

        Args:
            agent_id: Agent being terminated.
            active_tasks: Tasks currently assigned to the agent.

        Returns:
            Tasks transitioned to INTERRUPTED (cleared assigned_to).

        Raises:
            TaskReassignmentError: If task transition fails.
        """
        ...
