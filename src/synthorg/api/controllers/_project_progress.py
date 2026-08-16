# module-kind: controller
"""Assemble a project's initiative progress for the operator surface.

Joins the project, the plan it is executing, and the tasks implementing that
plan's items into one response: per-item status, derived counts, and the
critical path. Read-only; the rollup owns every status write.

A project with no plan yet (not dispatched, or created directly rather than
greenlit) returns its own status with an empty item list rather than a 404,
so the dashboard renders the same shape throughout an initiative's life.

This lives in the controller layer rather than ``api/services/`` because it
builds an API response DTO. A service must stay DTO-free so it can be reused
off the HTTP path (enforced by ``check_no_api_dto_in_persistence_or_service``);
the reusable part of this logic is the pure derivation in
``engine/initiative/``, which this assembles into the wire shape.
"""

import asyncio
from typing import Final
from uuid import UUID

from synthorg.api.dto_project_progress import (
    ProjectProgress,
    ProjectProgressCounts,
    ProjectProgressItem,
)
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.project import Project
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._ids import subtask_uuid
from synthorg.engine.initiative.completion import (
    ItemProgress,
    item_is_done,
    summarise_progress,
)
from synthorg.engine.initiative.contributors import initiative_contributors
from synthorg.engine.initiative.critical_path import longest_dependency_chain
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.task_protocol import TaskFilterSpec

#: Page size for draining a plan's tasks; a plan's items are bounded well below
#: this at the request boundary, so one page is the normal case.
_TASK_PAGE_SIZE: Final[int] = 200


class ProjectProgressAssembler:
    """Read-side projection of the project / plan / task graph.

    Args:
        persistence: Backend supplying the plan, task, and project repositories.
    """

    __slots__ = ("_persistence",)

    def __init__(self, *, persistence: PersistenceBackend) -> None:
        self._persistence = persistence

    async def for_project(self, project: Project) -> ProjectProgress:
        """Build the progress view for *project*.

        Returns:
            The assembled :class:`ProjectProgress`; items and counts are empty
            when the project has no plan yet.
        """
        # Keyed by PROJECT, not by plan: an initiative's objective task is not
        # a plan item, and the operator asking who worked this means everyone.
        # Neither read needs the other, so they share one round-trip window
        # rather than paying for two in series on a page load.
        async with asyncio.TaskGroup() as group:
            contributors_task = group.create_task(
                initiative_contributors(
                    self._persistence.tasks,
                    project_id=NotBlankStr(str(project.id)),
                    lead_id=NotBlankStr(project.lead) if project.lead else None,
                )
            )
            plan_task = group.create_task(self._plan_of(project))
        contributors = contributors_task.result()
        plan = plan_task.result()
        if plan is None:
            return ProjectProgress(
                project_id=project.id,
                project_status=project.status,
                contributors=contributors,
            )
        tasks = await self._tasks_by_item(plan)
        items = self._items(plan, tasks)
        critical = longest_dependency_chain(
            {
                str(item.item_id): tuple(str(dep) for dep in item.depends_on)
                for item in items
            }
        )
        on_path = {UUID(node) for node in critical}
        return ProjectProgress(
            project_id=project.id,
            project_status=project.status,
            contributors=contributors,
            plan_id=plan.id,
            plan_status=plan.status,
            objective_title=plan.objective_title,
            items=tuple(
                item.model_copy(update={"on_critical_path": item.item_id in on_path})
                for item in items
            ),
            counts=_counts(items),
            critical_path=tuple(UUID(node) for node in critical),
        )

    async def _plan_of(self, project: Project) -> Plan | None:
        """Resolve the plan the project is executing.

        Returns:
            The plan named by ``project.plan_id``, or ``None`` when the project
            has not dispatched one (or its plan row has since been removed).
        """
        if project.plan_id is None:
            return None
        return await self._persistence.plans.get(NotBlankStr(str(project.plan_id)))

    async def _tasks_by_item(self, plan: Plan) -> dict[UUID, Task]:
        """Index the plan's dispatched tasks by the item each implements.

        Returns:
            Map of plan-item id to the task implementing it.
        """
        indexed: dict[UUID, Task] = {}
        offset = 0
        # lint-allow: long-running-loop-kill-switch -- bounded by plan size
        while True:
            page = await self._persistence.tasks.query(
                TaskFilterSpec(plan=plan.id),
                limit=_TASK_PAGE_SIZE,
                offset=offset,
            )
            for task in page:
                if task.plan_item_id is not None:
                    indexed[task.plan_item_id] = task
            if len(page) < _TASK_PAGE_SIZE:
                return indexed
            offset += _TASK_PAGE_SIZE

    def _items(
        self,
        plan: Plan,
        tasks: dict[UUID, Task],
    ) -> tuple[ProjectProgressItem, ...]:
        """Project each plan item and its task into the response shape.

        Returns:
            One :class:`ProjectProgressItem` per plan item, in plan order.
        """
        projected: list[ProjectProgressItem] = []
        for item in plan.items:
            item_uuid = subtask_uuid(item.id)
            task = tasks.get(item_uuid)
            chosen = (
                item.chosen_option_id if item.kind is PlanItemKind.DECISION else None
            )
            done = item_is_done(
                ItemProgress(
                    item_id=item_uuid,
                    kind=item.kind,
                    task_id=task.id if task is not None else None,
                    task_status=task.status if task is not None else None,
                    blocked_reason=task.blocked_reason if task is not None else None,
                    chosen_option_id=chosen,
                )
            )
            projected.append(
                ProjectProgressItem(
                    item_id=item_uuid,
                    title=item.title,
                    kind=item.kind,
                    owner=item.owner,
                    depends_on=tuple(subtask_uuid(dep) for dep in item.dependencies),
                    task_id=task.id if task is not None else None,
                    task_status=task.status if task is not None else None,
                    blocked_reason=task.blocked_reason if task is not None else None,
                    chosen_option_id=chosen,
                    done=done,
                )
            )
        return tuple(projected)


def _counts(items: tuple[ProjectProgressItem, ...]) -> ProjectProgressCounts:
    """Derive the attention counts across *items*.

    Returns:
        The :class:`ProjectProgressCounts` for the plan.
    """
    summary = summarise_progress(
        tuple(
            ItemProgress(
                item_id=item.item_id,
                kind=item.kind,
                task_id=item.task_id,
                task_status=item.task_status,
                blocked_reason=item.blocked_reason,
                chosen_option_id=item.chosen_option_id,
            )
            for item in items
        )
    )
    return ProjectProgressCounts(
        total=summary.total,
        done=summary.done,
        failed=summary.failed,
        blocked=summary.blocked,
    )
