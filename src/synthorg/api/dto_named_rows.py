# module-kind: declarative
"""The rows the dashboard reads, with every actor reference already named.

Each view extends the entity rather than restating it, so a field added to
the domain model reaches the surface without a second declaration to keep in
step. What the view adds is the name beside the id, resolved per response by
:mod:`synthorg.api._read_names`.

``None`` is the honest answer for an actor with no name (retired, or from
another organisation), and the surface renders its own words for that. It is
never the id: a field a browser can print is a field a browser will print.

Each row is rebuilt from ``dict(entity)``, which yields the declared fields
only. ``model_dump()`` also emits every ``@computed_field``, and a frozen
model forbidding extras rejects its own derived values on the way back in, so
one computed field anywhere in an entity's tree would 500 the endpoint.
"""

from collections.abc import Iterable, Mapping
from typing import Self

from pydantic import Field

from synthorg.api._read_names import resolved_actor_name
from synthorg.budget.coordination_store import CoordinationMetricsRecord
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.project import Project
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.models import LivingDocument
from synthorg.security.models import AuditEntry


class ProjectRow(Project):
    """A project, plus the name of the agent leading it."""

    lead_name: NotBlankStr | None = Field(
        default=None,
        description="Display name of the project lead, when the lead has one",
    )

    @classmethod
    def of(cls, project: Project, names: Mapping[str, str]) -> Self:
        """Build the row for *project*.

        Returns:
            The project with its lead resolved.
        """
        return cls(
            **dict(project),
            lead_name=_as_name(resolved_actor_name(project.lead, names)),
        )


class AuditEntryRow(AuditEntry):
    """An audit entry, plus the name of the agent it records.

    The entry itself is immutable and stores the id, which is what makes it
    correlatable long after the agent is gone. The name is added here so the
    log reads as a record of people rather than of keys.
    """

    agent_name: NotBlankStr | None = Field(
        default=None,
        description="Display name of the recorded agent, when they have one",
    )

    @classmethod
    def of(cls, entry: AuditEntry, names: Mapping[str, str]) -> Self:
        """Build the row for *entry*.

        Returns:
            The entry with its agent resolved.
        """
        return cls(
            **dict(entry),
            agent_name=_as_name(resolved_actor_name(entry.agent_id, names)),
        )


class CoordinationMetricsRow(CoordinationMetricsRecord):
    """One coordination run, with the task and lead agent both named.

    ``agent_id`` is ``None`` for a system-level run, which is a different
    statement from "an agent whose name we could not resolve"; the surface
    keeps them apart, so this row resolves only what is there.
    """

    task_title: NotBlankStr | None = Field(
        default=None,
        description="Title of the run's task, when it is still readable",
    )
    agent_name: NotBlankStr | None = Field(
        default=None,
        description="Display name of the lead agent, when there is one",
    )

    @classmethod
    def of(
        cls,
        record: CoordinationMetricsRecord,
        names: Mapping[str, str],
        titles: Mapping[str, str],
    ) -> Self:
        """Build the row for *record*.

        Returns:
            The record with its task and lead agent resolved.
        """
        return cls(
            **dict(record),
            task_title=_as_name(titles.get(record.task_id)),
            agent_name=_as_name(resolved_actor_name(record.agent_id, names)),
        )


class LivingDocumentRow(LivingDocument):
    """A living document, plus the name of the agent that last wrote it."""

    author_name: NotBlankStr | None = Field(
        default=None,
        description="Display name of the last author, when they have one",
    )

    @classmethod
    def of(cls, doc: LivingDocument, names: Mapping[str, str]) -> Self:
        """Build the row for *doc*.

        Returns:
            The document with its author resolved.
        """
        return cls(
            **dict(doc),
            author_name=_as_name(resolved_actor_name(doc.author_agent_id, names)),
        )


class PlanItemRow(PlanItem):
    """A plan item, plus the name of whoever owns it.

    ``owner`` holds a role or an agent. A role is already a word a person
    reads and comes back unchanged; an agent id resolves to the roster name,
    or to ``None`` when nothing on the roster answers for it.
    """

    owner_name: NotBlankStr | None = Field(
        default=None,
        description="Display name of the item's owner, when the owner has one",
    )

    @classmethod
    def of(cls, item: PlanItem, names: Mapping[str, str]) -> Self:
        """Build the row for *item*.

        Returns:
            The item with its owner resolved.
        """
        return cls(
            **dict(item),
            owner_name=_as_name(resolved_actor_name(item.owner, names)),
        )


class PlanRow(Plan):
    """A plan whose items each name their owner.

    ``items`` narrows the base field to the row type. Read-only on the wire,
    so the narrowing is safe in the direction that matters: a caller is handed
    rows, and nothing writes a bare item back through this shape.
    """

    items: tuple[PlanItemRow, ...] = Field(  # pyright: ignore[reportIncompatibleVariableOverride] -- frozen, so the narrowing is read-only
        description="Ordered plan items"
    )

    @classmethod
    def of(cls, plan: Plan, names: Mapping[str, str]) -> Self:
        """Build the row for *plan*.

        Returns:
            The plan with every item's owner resolved.
        """
        rows = [PlanItemRow.of(item, names) for item in plan.items]
        return cls(**(dict(plan) | {"items": rows}))


class TaskRow(Task):
    """A task, plus the name of the agent it is assigned to."""

    assigned_to_name: NotBlankStr | None = Field(
        default=None,
        description="Display name of the assignee, when the assignee has one",
    )

    @classmethod
    def of(cls, task: Task, names: Mapping[str, str]) -> Self:
        """Build the row for *task*.

        Returns:
            The task with its assignee resolved.
        """
        return cls(
            **dict(task),
            assigned_to_name=_as_name(resolved_actor_name(task.assigned_to, names)),
        )


def _as_name(value: str | None) -> NotBlankStr | None:
    """Narrow a resolved name to the non-blank type the field declares.

    Returns:
        The name, or ``None`` when there is none to show.
    """
    return NotBlankStr(value) if value else None


def audit_rows(
    entries: Iterable[AuditEntry], names: Mapping[str, str]
) -> tuple[AuditEntryRow, ...]:
    """Name the recorded agent on every entry in *entries*.

    Returns:
        The rows, in order.
    """
    return tuple(AuditEntryRow.of(entry, names) for entry in entries)


def plan_rows(plans: Iterable[Plan], names: Mapping[str, str]) -> tuple[PlanRow, ...]:
    """Name every item's owner across *plans*.

    Returns:
        The rows, in order.
    """
    return tuple(PlanRow.of(plan, names) for plan in plans)


def project_rows(
    projects: Iterable[Project], names: Mapping[str, str]
) -> tuple[ProjectRow, ...]:
    """Name the lead on every project in *projects*.

    Returns:
        The rows, in order.
    """
    return tuple(ProjectRow.of(project, names) for project in projects)


def task_rows(tasks: Iterable[Task], names: Mapping[str, str]) -> tuple[TaskRow, ...]:
    """Name the assignee on every task in *tasks*.

    Returns:
        The rows, in order.
    """
    return tuple(TaskRow.of(task, names) for task in tasks)


__all__ = [
    "AuditEntryRow",
    "CoordinationMetricsRow",
    "LivingDocumentRow",
    "PlanItemRow",
    "PlanRow",
    "ProjectRow",
    "TaskRow",
    "audit_rows",
    "plan_rows",
    "project_rows",
    "task_rows",
]
