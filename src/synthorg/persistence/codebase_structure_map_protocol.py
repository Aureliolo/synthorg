"""Codebase structure-map repository protocol."""

from typing import Protocol, runtime_checkable

from synthorg.core.codebase_structure_map import CodebaseStructureMap
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    IdKeyedRepository,
)


@runtime_checkable
class CodebaseStructureMapRepository(
    IdKeyedRepository[CodebaseStructureMap, NotBlankStr],
    Protocol,
):
    """CRUD interface for :class:`CodebaseStructureMap` persistence.

    Keyed 1:1 by ``project_id`` (mirrors
    :class:`~synthorg.persistence.project_workspace_protocol.ProjectWorkspaceRepository`):
    a structure map is always resolved by its owning project, never queried
    by attribute. ``save`` is an upsert because a same-source re-import
    re-scans in place.
    """

    async def save(self, entity: CodebaseStructureMap) -> None:
        """Persist a structure map via upsert (insert or update).

        Args:
            entity: The structure map to persist. ``entity.project_id`` is
                the primary key.

        Raises:
            QueryError: If the database operation fails.
        """
        ...

    async def get(self, entity_id: NotBlankStr) -> CodebaseStructureMap | None:
        """Retrieve a structure map by owning project id.

        Args:
            entity_id: The owning project identifier.

        Returns:
            The structure map, or ``None`` if the project has none.

        Raises:
            QueryError: If the query or deserialization fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CodebaseStructureMap, ...]:
        """List structure maps in project-id order.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Structure maps in ascending ``project_id`` order, capped at
            *limit* rows.

        Raises:
            QueryError: If the query or deserialization fails.
        """
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a structure map by owning project id.

        Args:
            entity_id: The owning project identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        ...
