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

import copy
from collections.abc import Iterable, Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.api._read_names import resolved_actor_name
from synthorg.budget.coordination_store import CoordinationMetricsRecord
from synthorg.core.artifact import Artifact
from synthorg.core.lifecycle_transition import LifecycleTransition
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


class ArtifactRow(Artifact):
    """An artifact, plus the name of the agent that produced it."""

    created_by_name: NotBlankStr | None = Field(
        default=None,
        description="Display name of the creating agent, when they have one",
    )

    @classmethod
    def of(cls, artifact: Artifact, names: Mapping[str, str]) -> Self:
        """Build the row for *artifact*.

        Returns:
            The artifact with its creator resolved.
        """
        return cls(
            **dict(artifact),
            created_by_name=_as_name(resolved_actor_name(artifact.created_by, names)),
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


class LifecycleTransitionRow(LifecycleTransition):
    """One recorded status change, with whoever asked for it named.

    ``requested_by`` is ``None`` when the system moved the entity on its own
    schedule, which the surface states in its own words; a stored reference
    that resolves to nothing is the same statement, so both arrive here as an
    absent name rather than as the reference itself.
    """

    requested_by_name: NotBlankStr | None = Field(
        default=None,
        description="Display name of whoever asked for the move, when named",
    )

    @classmethod
    def of(cls, transition: LifecycleTransition, names: Mapping[str, str]) -> Self:
        """Build the row for *transition*.

        Returns:
            The transition with its requester resolved.
        """
        return cls(
            **dict(transition),
            requested_by_name=_as_name(
                resolved_actor_name(transition.requested_by, names)
            ),
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


class PlanPendingDecision(BaseModel):
    """A decision waiting on the operator about one plan.

    Resolved beside the row rather than looked up by the browser, for the
    reason every other reference on these rows is: a client-side lookup of a
    key the row does not carry renders nothing on the first paint and nothing
    at all for a decision the fetched page did not cover.

    Attributes:
        approval_id: The item to navigate to. The link is LABELLED by the
            title and navigates by this.
        action_type: Which decision it is, so a surface can tell one kind from
            another without matching on prose.
        title: What the decision is called, as the operator reads it.
        reason: Why it was raised, in words rather than an enum value.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    approval_id: NotBlankStr = Field(description="The approval to navigate to")
    action_type: NotBlankStr = Field(description="Which decision this is")
    title: NotBlankStr = Field(description="What the decision is called")
    reason: NotBlankStr = Field(description="Why it was raised")


class PlanRow(Plan):
    """A plan whose items each name their owner.

    ``items`` narrows the base field to the row type. Read-only on the wire,
    so the narrowing is safe in the direction that matters: a caller is handed
    rows, and nothing writes a bare item back through this shape.

    ``pending_decision`` is derived rather than stored. The plan's status says
    what the organisation last did with it; it cannot say that the initiative
    has stopped and is waiting on a person, and an operator reading
    ``executing`` on a plan whose every item is dead is the surface lying to
    them. The open approval already holds that fact, so it is resolved here
    instead of duplicated onto the row.
    """

    items: tuple[PlanItemRow, ...] = Field(  # pyright: ignore[reportIncompatibleVariableOverride] -- frozen, so the narrowing is read-only
        description="Ordered plan items"
    )
    pending_decision: PlanPendingDecision | None = Field(
        default=None,
        description="The decision waiting on the operator about this plan",
    )

    @classmethod
    def of(
        cls,
        plan: Plan,
        names: Mapping[str, str],
        decisions: Mapping[str, PlanPendingDecision] | None = None,
    ) -> Self:
        """Build the row for *plan*.

        Args:
            plan: The plan to name the references of.
            names: Agent id to display name, from :func:`agent_name_map`.
            decisions: Plan id to the decision waiting on it, from
                :func:`pending_plan_decisions`. May cover a whole page,
                because the list read resolves once across every row; the row
                keeps only its own, so the field means the same thing on every
                surface that carries it.

        Returns:
            The plan with every item's owner resolved.
        """
        rows = [PlanItemRow.of(item, names) for item in plan.items]
        waiting = None if decisions is None else decisions.get(str(plan.id))
        return cls(**(dict(plan) | {"items": rows, "pending_decision": waiting}))


class TaskRow(Task):
    """A task, plus the names its own references stand for."""

    assigned_to_name: NotBlankStr | None = Field(
        default=None,
        description="Display name of the assignee, when the assignee has one",
    )
    dependency_titles: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Title of each dependency that resolved, keyed by its id. A"
            " dependency absent from the map is one nothing could name, which"
            " the surface words itself rather than printing the key"
        ),
    )

    @model_validator(mode="after")
    def _deep_copy_dependency_titles(self) -> Self:
        """Deep-copy dependency_titles so the frozen model cannot be aliased.

        `frozen=True` stops the field being reassigned and does nothing to the
        dict behind it, so without this a caller holding the mapping it passed
        in could still mutate a row it already handed over.

        Returns:
            The instance with ``dependency_titles`` deep-copied.
        """
        object.__setattr__(
            self, "dependency_titles", copy.deepcopy(self.dependency_titles)
        )
        return self

    @classmethod
    def of(
        cls,
        task: Task,
        names: Mapping[str, str],
        titles: Mapping[str, str] | None = None,
    ) -> Self:
        """Build the row for *task*.

        Args:
            task: The task to name the references of.
            names: Agent id to display name, from :func:`agent_name_map`.
            titles: Task id to title, from :func:`task_titles`. May cover a whole
                page, because the list read resolves once across every row; the
                row keeps only its own dependencies out of it, so the field means
                the same thing on every surface that carries it.

        Returns:
            The task with its assignee and its dependencies resolved.
        """
        resolved = titles or {}
        return cls(
            **dict(task),
            assigned_to_name=_as_name(resolved_actor_name(task.assigned_to, names)),
            dependency_titles={
                dependency: resolved[dependency]
                for dependency in task.dependencies
                if dependency in resolved
            },
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


def plan_rows(
    plans: Iterable[Plan],
    names: Mapping[str, str],
    decisions: Mapping[str, PlanPendingDecision] | None = None,
) -> tuple[PlanRow, ...]:
    """Name every item's owner across *plans*, and say which are waiting.

    Returns:
        The rows, in order.
    """
    return tuple(PlanRow.of(plan, names, decisions) for plan in plans)


def project_rows(
    projects: Iterable[Project], names: Mapping[str, str]
) -> tuple[ProjectRow, ...]:
    """Name the lead on every project in *projects*.

    Returns:
        The rows, in order.
    """
    return tuple(ProjectRow.of(project, names) for project in projects)


def task_rows(
    tasks: Iterable[Task],
    names: Mapping[str, str],
    titles: Mapping[str, str] | None = None,
) -> tuple[TaskRow, ...]:
    """Name the assignee and title the dependencies on every task in *tasks*.

    One title map for the whole page, resolved by the caller across every
    dependency it references, so a list row carries the same map a detail row
    would rather than a different one per row.

    Returns:
        The rows, in order.
    """
    return tuple(TaskRow.of(task, names, titles) for task in tasks)


__all__ = [
    "ArtifactRow",
    "AuditEntryRow",
    "CoordinationMetricsRow",
    "LifecycleTransitionRow",
    "LivingDocumentRow",
    "PlanItemRow",
    "PlanPendingDecision",
    "PlanRow",
    "ProjectRow",
    "TaskRow",
    "audit_rows",
    "plan_rows",
    "project_rows",
    "task_rows",
]
