"""Per-task factory for the research agent tool.

Binds the calling task's ``project_id`` and the acting agent to a fresh
``research`` tool. Constructed once at boot by ``_wire_research_engine``
and parked on the app state; the per-task tool loader calls
:meth:`build_tools` with the task scope.
"""

from typing import TYPE_CHECKING, Final

from synthorg.research.tool import ResearchTool

if TYPE_CHECKING:
    from collections.abc import Iterable

    from synthorg.core.clock import Clock
    from synthorg.core.types import NotBlankStr
    from synthorg.research.service import ResearchService
    from synthorg.tools.base import BaseTool

RESEARCH_TOOL_NAMES: Final[tuple[str, ...]] = ("research",)


class ResearchToolFactory:
    """Build the per-task research tool bound to a project + agent."""

    __slots__ = ("_clock", "_service")

    def __init__(
        self,
        *,
        service: ResearchService,
        clock: Clock | None = None,
    ) -> None:
        self._service = service
        self._clock = clock

    def build_tools(
        self,
        *,
        project_id: NotBlankStr | None,
        created_by: NotBlankStr,
    ) -> tuple[BaseTool, ...]:
        """Return the research tool bound to *project_id* and *created_by*."""
        return (
            ResearchTool(
                service=self._service,
                project_id=project_id,
                created_by=created_by,
                clock=self._clock,
            ),
        )

    def tool_names(self) -> Iterable[str]:
        """Inventory of tool names produced by this factory."""
        return RESEARCH_TOOL_NAMES


def build_research_tool_factory(
    *,
    service: ResearchService,
    clock: Clock | None = None,
) -> ResearchToolFactory:
    """Construct the default :class:`ResearchToolFactory` (boot wiring)."""
    return ResearchToolFactory(service=service, clock=clock)


__all__ = [
    "RESEARCH_TOOL_NAMES",
    "ResearchToolFactory",
    "build_research_tool_factory",
]
