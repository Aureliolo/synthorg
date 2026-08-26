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
from collections.abc import Iterable, Mapping
from typing import Final
from uuid import UUID

from synthorg.api._read_names import resolved_actor_name
from synthorg.api.dto_project_progress import (
    ContributorRef,
    ProjectProgress,
    ProjectProgressCounts,
    ProjectProgressItem,
)
from synthorg.core.pagination import collect_all
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
from synthorg.persistence.plan_protocol import PlanFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.task_protocol import TaskFilterSpec

#: Page size for draining a plan's tasks; a plan's items are bounded well below
#: this at the request boundary, so one page is the normal case.
_TASK_PAGE_SIZE: Final[int] = 200

#: Rows per query while the unlinked fallback drains a project's plans. Every
#: page is read, because the repository orders by id and the newest is wanted:
#: taking the newest of ONE page picks the newest of an arbitrary subset, which
#: for a project past this many plans is a different plan than the answer. A
#: project accumulates one plan per replan, so the drain is normally one page.
_PLAN_PAGE_SIZE: Final[int] = 200


class ProjectProgressAssembler:
    """Read-side projection of the project / plan / task graph.

    Args:
        persistence: Backend supplying the plan, task, and project repositories.
    """

    __slots__ = ("_agent_names", "_persistence")

    def __init__(
        self,
        *,
        persistence: PersistenceBackend,
        agent_names: Mapping[str, str],
    ) -> None:
        self._persistence = persistence
        self._agent_names = agent_names

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
                contributors=self._contributor_refs(contributors),
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
            contributors=self._contributor_refs(contributors),
            plan_id=plan.id,
            plan_status=plan.status,
            plan_failure_reason=plan.failure_reason,
            objective_title=plan.objective_title,
            items=tuple(
                item.model_copy(update={"on_critical_path": item.item_id in on_path})
                for item in items
            ),
            counts=_counts(items),
            critical_path=tuple(UUID(node) for node in critical),
        )

    def _contributor_refs(
        self, contributors: Iterable[str]
    ) -> tuple[ContributorRef, ...]:
        """Pair each contributing agent's id with the name they are known by.

        Returns:
            One ref per contributor, in order.
        """
        return tuple(
            ContributorRef(
                id=NotBlankStr(agent_id),
                name=_non_blank(resolved_actor_name(agent_id, self._agent_names)),
            )
            for agent_id in contributors
        )

    async def _plan_of(self, project: Project) -> Plan | None:
        """Resolve the plan this project's progress is about.

        ``project.plan_id`` is written at DISPATCH, so a plan that died before
        it, in decomposition or at the approval gate, is never linked. Reading
        only the link therefore reported "no plan yet" for a project whose plan
        existed and had failed with a recorded reason, which is the one thing
        the operator opening this page needs to know. So the link is preferred
        and the project's own plans are the fallback.

        Returns:
            The plan named by ``project.plan_id``; else the project's most
            recent plan; else ``None`` when it has never had one.
        """
        if project.plan_id is not None:
            linked = await self._persistence.plans.get(
                NotBlankStr(str(project.plan_id))
            )
            if linked is not None:
                return linked
        # Drained rather than read one page deep: the repository orders by id
        # and this wants the newest by creation, so a single page is an
        # arbitrary subset to take a maximum over.
        plans = await collect_all(
            lambda limit, offset: self._persistence.plans.query(
                PlanFilterSpec(project=NotBlankStr(str(project.id))),
                limit=limit,
                offset=offset,
            ),
            page_size=_PLAN_PAGE_SIZE,
        )
        if not plans:
            return None
        return max(plans, key=lambda plan: plan.created_at)

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
                    owner_name=_non_blank(
                        resolved_actor_name(item.owner, self._agent_names)
                    ),
                    depends_on=tuple(subtask_uuid(dep) for dep in item.dependencies),
                    task_id=task.id if task is not None else None,
                    task_status=task.status if task is not None else None,
                    blocked_reason=task.blocked_reason if task is not None else None,
                    chosen_option_id=chosen,
                    done=done,
                )
            )
        return tuple(projected)


def _non_blank(value: str | None) -> NotBlankStr | None:
    """Narrow a resolved name to the non-blank type the ref declares.

    Returns:
        The name, or ``None`` when there is none to show.
    """
    return NotBlankStr(value) if value else None


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
