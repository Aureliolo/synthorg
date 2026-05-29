"""Per-task factory for the knowledge agent tools.

Binds the calling task's ``project_id`` to fresh ``search_knowledge`` and
``ingest_knowledge`` tools. Constructed once at boot by
``_wire_knowledge_engine`` and parked on the app state; the per-task
tool-loader calls :meth:`build_tools` with the task's project scope.
"""

from typing import TYPE_CHECKING, Final

from synthorg.tools.knowledge.ingest_knowledge import IngestKnowledgeTool
from synthorg.tools.knowledge.search_knowledge import SearchKnowledgeTool

if TYPE_CHECKING:
    from collections.abc import Iterable

    from synthorg.core.types import NotBlankStr
    from synthorg.knowledge.service import KnowledgeService
    from synthorg.tools.base import BaseTool

KNOWLEDGE_TOOL_NAMES: Final[tuple[str, ...]] = (
    "search_knowledge",
    "ingest_knowledge",
)


class KnowledgeToolFactory:
    """Build per-task knowledge tools bound to a project scope."""

    __slots__ = ("_service",)

    def __init__(self, *, service: KnowledgeService) -> None:
        self._service = service

    def build_tools(
        self,
        *,
        project_id: NotBlankStr | None,
    ) -> tuple[BaseTool, ...]:
        """Return both knowledge tools bound to *project_id* (None = global)."""
        return (
            SearchKnowledgeTool(service=self._service, project_id=project_id),
            IngestKnowledgeTool(service=self._service, project_id=project_id),
        )

    def tool_names(self) -> Iterable[str]:
        """Inventory of tool names produced by this factory.

        Returns:
            The static tuple of knowledge tool names this factory emits.
        """
        return KNOWLEDGE_TOOL_NAMES


def build_knowledge_tool_factory(
    *,
    service: KnowledgeService,
) -> KnowledgeToolFactory:
    """Construct the default :class:`KnowledgeToolFactory` (boot wiring).

    Returns:
        A ``KnowledgeToolFactory`` bound to ``service``.
    """
    return KnowledgeToolFactory(service=service)


__all__ = [
    "KNOWLEDGE_TOOL_NAMES",
    "KnowledgeToolFactory",
    "build_knowledge_tool_factory",
]
