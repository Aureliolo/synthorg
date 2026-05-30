"""Per-task factory for the living-doc agent tools.

The static :func:`build_default_tools_from_config` registry at boot is
not the right home for these tools because they bind ``project_id`` and
``author_agent_id`` from the per-task execution context, not from
deployment config. This factory is called by the per-task tool-loader
when an agent's task carries a ``project_id`` and the docs engine is
wired; it returns the two agent tools fresh per invocation so the
binding stays scoped to the calling task.

The boot wiring (see ``api/app.py::_wire_docs_engine``) constructs a
default factory callable and parks it on the app state behind
:meth:`AppState.set_docs_tool_factory`; downstream callers (an
execution context, a future tool-loader hook) consume the factory
without re-importing :class:`DocsService`.
"""

from typing import TYPE_CHECKING, Final

from synthorg.tools.docs.search_living_docs import SearchLivingDocsTool
from synthorg.tools.docs.write_living_doc import WriteLivingDocTool

if TYPE_CHECKING:
    from collections.abc import Iterable

    from synthorg.core.types import NotBlankStr
    from synthorg.docs_engine.service import DocsService
    from synthorg.tools.base import BaseTool


DOCS_TOOL_NAMES: Final[tuple[str, ...]] = (
    "write_living_doc",
    "search_living_docs",
)


class DocsToolFactory:
    """Build per-task living-doc tools bound to a project + author."""

    __slots__ = ("_docs_service",)

    def __init__(self, *, docs_service: DocsService) -> None:
        self._docs_service = docs_service

    def build_tools(
        self,
        *,
        project_id: NotBlankStr,
        author_agent_id: NotBlankStr,
    ) -> tuple[BaseTool, ...]:
        """Return both docs tools bound to *project_id* + *author_agent_id*.

        Returned tools are fresh per call; the caller owns them for the
        duration of one agent task. The factory itself holds no
        per-task state.
        """
        return (
            WriteLivingDocTool(
                docs_service=self._docs_service,
                project_id=project_id,
                author_agent_id=author_agent_id,
            ),
            SearchLivingDocsTool(
                docs_service=self._docs_service,
                project_id=project_id,
            ),
        )

    def tool_names(self) -> Iterable[str]:
        """Inventory of tool names produced by this factory.

        Returns:
            The static tuple of docs tool names this factory emits.
        """
        return DOCS_TOOL_NAMES


def build_docs_tool_factory(*, docs_service: DocsService) -> DocsToolFactory:
    """Construct the default :class:`DocsToolFactory`.

    Called once at boot by ``_wire_docs_engine``. Keeping construction
    in a named factory function lets the ghost-wiring gate enforce
    that the tool surface is reachable from the shipped boot path,
    even though the tools themselves are instantiated per-task.

    Returns:
        A ``DocsToolFactory`` bound to ``docs_service``.
    """
    return DocsToolFactory(docs_service=docs_service)


__all__ = [
    "DOCS_TOOL_NAMES",
    "DocsToolFactory",
    "build_docs_tool_factory",
]
