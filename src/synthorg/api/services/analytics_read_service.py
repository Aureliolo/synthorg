"""AnalyticsReadService: the analytics controllers' persistence seam.

The ``/analytics/overview`` and ``/analytics/trends`` endpoints assemble
their DTOs from cost / agent / performance services plus the task list.
The cost / agent / performance reads already route through their own
services; this service owns the one remaining direct repository touch
(the task-list query) so the analytics controllers never reach into
``persistence.tasks`` directly and gain a single mockable seam.
"""

from synthorg.core.task import Task
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository


class AnalyticsReadService:
    """Read-only facade over the task repository for analytics endpoints.

    Args:
        task_repo: Repository handling :class:`Task` rows.
    """

    __slots__ = ("_task_repo",)

    def __init__(self, *, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo

    async def list_tasks(self) -> tuple[Task, ...]:
        """Return the task list the analytics aggregates are built from.

        Mirrors the prior controller behaviour (an unfiltered ``query``
        with the repository's default page window), so a caller swapping
        the direct repo touch for this method observes identical results.

        Returns:
            Tasks matching the empty filter spec, ordered by id ascending.
        """
        return await self._task_repo.query(TaskFilterSpec())


__all__ = ["AnalyticsReadService"]
