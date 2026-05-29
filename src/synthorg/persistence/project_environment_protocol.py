"""Project environment repository protocol."""

from typing import Protocol, override, runtime_checkable

from synthorg.core.project_environment import ProjectEnvironment
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    IdKeyedRepository,
)


@runtime_checkable
class ProjectEnvironmentRepository(
    IdKeyedRepository[ProjectEnvironment, NotBlankStr],
    Protocol,
):
    """CRUD interface for :class:`ProjectEnvironment` persistence.

    Keyed 1:1 by ``project_id``.  The surface is intentionally minimal:
    an environment row is always resolved by its owning project, never
    queried by attribute.  ``save`` is an upsert because
    :class:`~synthorg.engine.workspace.environment.service.EnvironmentService`
    re-provisions in place when the declaration hash or type changes (no
    separate create/update audit split is needed; provisioning is not an
    end-user CRUD surface).
    """

    @override
    async def save(self, entity: ProjectEnvironment) -> None:
        """Persist a project environment via upsert (insert or update).

        Args:
            entity: The environment row to persist.  ``entity.project_id``
                is the primary key.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr) -> ProjectEnvironment | None:
        """Retrieve a project environment by owning project id.

        Args:
            entity_id: The owning project identifier.

        Returns:
            The environment, or ``None`` if the project has none.

        Raises:
            QueryError: If the query or deserialization fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ProjectEnvironment, ...]:
        """List environments in project-id order.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Environments in ascending ``project_id`` order, capped at
            *limit* rows.

        Raises:
            QueryError: If the query or deserialization fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a project environment by owning project id.

        Args:
            entity_id: The owning project identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        ...
