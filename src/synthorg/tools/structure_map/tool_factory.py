"""Per-task factory for the structure-map agent tool.

Binds the calling task's ``project_id`` to a fresh ``query_structure_map``
tool over the codebase structure-map repository. Constructed once at boot
by the brownfield-intake wiring and parked on the app state; the per-task
tool-loader calls :meth:`build_tools` with the task's project scope.
"""

from typing import TYPE_CHECKING, Final

from synthorg.tools.structure_map.query_structure_map import QueryStructureMapTool

if TYPE_CHECKING:
    from collections.abc import Iterable

    from synthorg.core.types import NotBlankStr
    from synthorg.persistence.codebase_structure_map_protocol import (
        CodebaseStructureMapRepository,
    )
    from synthorg.tools.base import BaseTool

STRUCTURE_MAP_TOOL_NAMES: Final[tuple[str, ...]] = ("query_structure_map",)


class StructureMapToolFactory:
    """Build the per-task structure-map tool bound to a project scope."""

    __slots__ = ("_repository",)

    def __init__(self, *, repository: CodebaseStructureMapRepository) -> None:
        self._repository = repository

    def build_tools(
        self,
        *,
        project_id: NotBlankStr,
    ) -> tuple[BaseTool, ...]:
        """Return the structure-map query tool bound to *project_id*."""
        return (
            QueryStructureMapTool(
                repository=self._repository,
                project_id=project_id,
            ),
        )

    def tool_names(self) -> Iterable[str]:
        """Inventory of tool names produced by this factory."""
        return STRUCTURE_MAP_TOOL_NAMES


def build_structure_map_tool_factory(
    *,
    repository: CodebaseStructureMapRepository,
) -> StructureMapToolFactory:
    """Construct the default :class:`StructureMapToolFactory` (boot wiring)."""
    return StructureMapToolFactory(repository=repository)


__all__ = [
    "STRUCTURE_MAP_TOOL_NAMES",
    "StructureMapToolFactory",
    "build_structure_map_tool_factory",
]
