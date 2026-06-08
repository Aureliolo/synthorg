"""Project repository protocol."""

from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)


class ProjectFilterSpec(BaseModel):
    """Filter spec for ``ProjectRepository.query``.

    All fields are optional; an empty spec matches all projects.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    status: ProjectStatus | None = Field(default=None)
    lead: NotBlankStr | None = Field(default=None)


@runtime_checkable
class ProjectRepository(
    IdKeyedRepository[Project, NotBlankStr],
    FilteredQueryRepository[Project, ProjectFilterSpec],
    Protocol,
):
    """CRUD + query interface for Project persistence.

    The mutation surface is split into atomic ``create``/``update``
    methods so the service layer can attach the correct
    ``API_PROJECT_CREATED`` / ``API_PROJECT_UPDATED`` audit event
    without a TOCTOU ``get`` + ``save`` race.  ``save`` remains as an
    upsert convenience for callers that genuinely need
    "persist regardless of prior state" semantics (migration / import
    paths); production CRUD must go through the explicit pair.

    Composes :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`.
    ``create`` and ``update`` are atomic lifecycle transitions that the
    generic ``save`` (upsert) cannot distinguish; they preserve separate
    create-vs-update audit semantics that the service layer depends on.
    """

    async def create(self, project: Project) -> None:
        """Insert a new project, failing if the id already exists.

        Atomic insert-only operation paired with :meth:`update` to
        preserve distinct create vs. update audit events. See :meth:`save`
        for the upsert convenience when lifecycle is unknown.

        Args:
            project: The project to insert.

        Raises:
            DuplicateRecordError: A project with the same id is
                already persisted.
            QueryError: If the database operation fails.
        """
        ...

    async def update(self, project: Project) -> None:
        """Update an existing project, failing if no row matches.

        Atomic update-only operation paired with :meth:`create` to
        preserve distinct create vs. update audit events. See :meth:`save`
        for the upsert convenience when lifecycle is unknown.

        Args:
            project: The project to update.  ``project.id`` selects
                the row.

        Raises:
            RecordNotFoundError: No project with this id exists.
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def save(self, entity: Project) -> None:
        """Persist a project via upsert (insert or update).

        Used for migration / import paths that legitimately do not
        know whether the row exists.  Production CRUD endpoints must
        use :meth:`create` / :meth:`update` so the API audit event
        reflects the actual lifecycle.

        Args:
            entity: The project to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> Project | None:
        """Retrieve a project by its ID.

        Args:
            entity_id: The project identifier.

        Returns:
            The project, or ``None`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Project, ...]:
        """List all projects in ID order.

        Args:
            limit: Maximum projects to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Projects in ascending ID order, capped at *limit* rows.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: ProjectFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Project, ...]:
        """List projects matching the filter spec.

        Results are ordered by project ID ascending to ensure
        deterministic pagination across backends.

        Args:
            filter_spec: Carries optional ``status`` and ``lead`` filters.
            limit: Maximum projects to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Matching projects ordered by ID, capped at *limit* rows.

        Raises:
            QueryError: If the operation fails.
        """
        ...

    @override
    async def count(self, filter_spec: ProjectFilterSpec) -> int:
        """Count projects matching the filter spec."""
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a project by ID.

        Args:
            entity_id: The project identifier.

        Returns:
            ``True`` if the project was deleted, ``False`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        ...
