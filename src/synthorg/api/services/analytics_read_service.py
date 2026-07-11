"""AnalyticsReadService: the analytics controllers' persistence seam.

The ``/analytics/overview`` and ``/analytics/trends`` endpoints assemble
their DTOs from cost / agent / performance services plus the task list.
The cost / agent / performance reads already route through their own
services; this service owns the one remaining direct repository touch
(the task-list query) so the analytics controllers never reach into
``persistence.tasks`` directly and gain a single mockable seam.
"""

from typing import Final

from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.observability import get_logger
from synthorg.observability.events.analytics import ANALYTICS_TASK_LIST_COLLECTED
from synthorg.persistence.task_protocol import TaskFilterSpec, TaskRepository

logger = get_logger(__name__)

# Batch size for the fetch-all pagination loop. Any positive size yields the
# complete set; this only bounds how many rows are read per round-trip.
_FETCH_PAGE_SIZE: Final[int] = 100


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

        Pages through the repository until exhausted: the analytics overview /
        trends endpoints fold the FULL task set into their counts, so a single
        default-window ``query`` (capped at the repository's default page size)
        would silently undercount once the org exceeds that many tasks.

        Returns:
            Every task matching the empty filter spec, ordered by id
            ascending.
        """
        tasks = await self._query_all(TaskFilterSpec())
        logger.debug(ANALYTICS_TASK_LIST_COLLECTED, task_count=len(tasks))
        return tasks

    async def list_in_progress(self) -> tuple[Task, ...]:
        """Return only the tasks currently executing (``IN_PROGRESS``).

        Utilisation surfaces (per-department health) need just the in-flight
        tasks to derive busy assignees, so this filters at the repository
        rather than scanning the whole task set per department.

        Returns:
            Every ``IN_PROGRESS`` task, ordered by id ascending.
        """
        tasks = await self._query_all(TaskFilterSpec(status=TaskStatus.IN_PROGRESS))
        logger.debug(ANALYTICS_TASK_LIST_COLLECTED, task_count=len(tasks))
        return tasks

    async def _query_all(self, spec: TaskFilterSpec) -> tuple[Task, ...]:
        """Page the repository in ``_FETCH_PAGE_SIZE`` windows until exhausted.

        Returns:
            Every task matching ``spec``, ordered by id ascending.
        """
        tasks: list[Task] = []
        offset = 0
        page = await self._task_repo.query(spec, limit=_FETCH_PAGE_SIZE, offset=offset)
        # Condition-driven (not ``while True``) so the fetch-all pagination
        # terminates on the first short page and reads as the bounded loop it
        # is, never a daemon needing a kill-switch.
        while page:
            tasks.extend(page)
            if len(page) < _FETCH_PAGE_SIZE:
                break
            offset += _FETCH_PAGE_SIZE
            page = await self._task_repo.query(
                spec, limit=_FETCH_PAGE_SIZE, offset=offset
            )
        return tuple(tasks)


__all__ = ["AnalyticsReadService"]
