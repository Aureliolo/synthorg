"""Shared in-memory workspace repo for docs-engine integration tests."""

from synthorg.core.project_workspace import ProjectWorkspace
from synthorg.core.types import NotBlankStr


class InMemoryWorkspaceRepo:
    """Minimal in-memory ``ProjectWorkspaceRepository`` test double."""

    def __init__(self) -> None:
        self._rows: dict[str, ProjectWorkspace] = {}

    async def save(self, entity: ProjectWorkspace) -> None:
        self._rows[entity.project_id] = entity

    async def get(self, entity_id: NotBlankStr) -> ProjectWorkspace | None:
        return self._rows.get(entity_id)

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ProjectWorkspace, ...]:
        rows = sorted(self._rows.values(), key=lambda r: r.project_id)
        return tuple(rows[offset : offset + limit])

    async def delete(self, entity_id: NotBlankStr) -> bool:
        return self._rows.pop(entity_id, None) is not None
