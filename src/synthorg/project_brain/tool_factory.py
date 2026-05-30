"""Per-task factory for the project-brain agent tools.

The static boot-time tool registry is not the right home for these tools: they
bind ``project_id`` and ``author_agent_id`` from the per-task execution context,
not from deployment config. The per-task tool-loader calls this factory when an
agent's task carries a ``project_id`` and the brain engine is wired; it returns
the two agent tools fresh per invocation so the binding stays scoped to the
calling task.

The boot wiring constructs a default factory and parks it on the app state;
downstream callers consume the factory without re-importing
:class:`ProjectBrainService`.
"""

from typing import TYPE_CHECKING, Final

from synthorg.tools.brain.search_brain import SearchBrainTool
from synthorg.tools.brain.write_brain_entry import WriteBrainEntryTool

if TYPE_CHECKING:
    from collections.abc import Iterable

    from synthorg.core.types import NotBlankStr
    from synthorg.project_brain.service import ProjectBrainService
    from synthorg.tools.base import BaseTool


BRAIN_TOOL_NAMES: Final[tuple[str, ...]] = (
    "write_brain_entry",
    "search_brain",
)


class ProjectBrainToolFactory:
    """Build per-task brain tools bound to a project + author."""

    __slots__ = ("_brain_service",)

    def __init__(self, *, brain_service: ProjectBrainService) -> None:
        self._brain_service = brain_service

    def build_tools(
        self,
        *,
        project_id: NotBlankStr,
        author_agent_id: NotBlankStr,
    ) -> tuple[BaseTool, ...]:
        """Return both brain tools bound to *project_id* + *author_agent_id*.

        Returned tools are fresh per call; the caller owns them for the duration
        of one agent task. The factory itself holds no per-task state.

        Returns:
            The write and search brain tools for this task.
        """
        return (
            WriteBrainEntryTool(
                brain_service=self._brain_service,
                project_id=project_id,
                author_agent_id=author_agent_id,
            ),
            SearchBrainTool(
                brain_service=self._brain_service,
                project_id=project_id,
            ),
        )

    def tool_names(self) -> Iterable[str]:
        """Inventory of tool names produced by this factory.

        Returns:
            The static tuple of brain tool names this factory emits.
        """
        return BRAIN_TOOL_NAMES


def build_project_brain_tool_factory(
    *,
    brain_service: ProjectBrainService,
) -> ProjectBrainToolFactory:
    """Construct the default :class:`ProjectBrainToolFactory`.

    Called once at boot by the brain wiring. Keeping construction in a named
    factory lets the ghost-wiring gate enforce that the tool surface is
    reachable from the shipped boot path, even though the tools are instantiated
    per-task.

    Returns:
        A ``ProjectBrainToolFactory`` bound to ``brain_service``.
    """
    return ProjectBrainToolFactory(brain_service=brain_service)


__all__ = [
    "BRAIN_TOOL_NAMES",
    "ProjectBrainToolFactory",
    "build_project_brain_tool_factory",
]
