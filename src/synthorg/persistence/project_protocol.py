"""Project repository protocol."""

from typing import Protocol, runtime_checkable

from synthorg.core.enums import ProjectStatus  # noqa: TC001
from synthorg.core.project import Project  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.persistence._shared import DEFAULT_LIST_LIMIT


@runtime_checkable
class ProjectRepository(Protocol):
    """CRUD + query interface for Project persistence.

    The mutation surface is split into atomic ``create``/``update``
    methods so the service layer can attach the correct
    ``API_PROJECT_CREATED`` / ``API_PROJECT_UPDATED`` audit event
    without a TOCTOU ``get`` + ``save`` race.  ``save`` remains as an
    upsert convenience for callers that genuinely need
    "persist regardless of prior state" semantics (migration / import
    paths); production CRUD must go through the explicit pair.
    """

    async def create(self, project: Project) -> None:
        """Insert a new project, failing if the id already exists.

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

        Args:
            project: The project to update.  ``project.id`` selects
                the row.

        Raises:
            RecordNotFoundError: No project with this id exists.
            QueryError: If the database operation fails.
        """
        ...

    async def save(self, project: Project) -> None:
        """Persist a project via upsert (insert or update).

        Used for migration / import paths that legitimately do not
        know whether the row exists.  Production CRUD endpoints must
        use :meth:`create` / :meth:`update` so the API audit event
        reflects the actual lifecycle.

        Args:
            project: The project to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    async def get(self, project_id: NotBlankStr) -> Project | None:
        """Retrieve a project by its ID.

        Args:
            project_id: The project identifier.

        Returns:
            The project, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_projects(
        self,
        *,
        status: ProjectStatus | None = None,
        lead: NotBlankStr | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[Project, ...]:
        """List projects with optional filters.

        Results are ordered by project ID ascending to ensure
        deterministic pagination across backends. Bounded by *limit* so
        an unauth'd caller cannot materialise the full table.

        Args:
            status: Filter by project status.
            lead: Filter by project lead agent ID.
            limit: Maximum projects to return (default
                :data:`DEFAULT_LIST_LIMIT`).

        Returns:
            Matching projects ordered by ID, capped at *limit* rows.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(self, project_id: NotBlankStr) -> bool:
        """Delete a project by ID.

        Args:
            project_id: The project identifier.

        Returns:
            ``True`` if the project was deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
