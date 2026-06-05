"""Per-task registry augmentation for the project-brain agent tools.

Mirrors ``registry_with_memory_tools`` / ``registry_with_external_api_tool``:
the engine's per-task tool-invoker factory calls this to append the two
project-brain tools (``write_brain_entry`` / ``search_brain``), freshly
bound to the calling task's ``project_id`` and author agent, so an agent
working a project can record decisions and recall them on re-entry. Returns
the registry unchanged when the brain is not wired (no factory) so the
augmentation is a no-op on a boot without a memory backend.
"""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from synthorg.project_brain.tool_factory import ProjectBrainToolFactory


def registry_with_brain_tools(
    tool_registry: ToolRegistry,
    factory: ProjectBrainToolFactory | None,
    *,
    project_id: str,
    author_agent_id: str,
) -> ToolRegistry:
    """Append the project-brain tools bound to *project_id* + *author_agent_id*.

    Args:
        tool_registry: Base per-task tool registry.
        factory: The project-brain tool factory, or ``None`` when the brain
            is not wired (then the registry is returned unchanged).
        project_id: The calling task's project identifier.
        author_agent_id: The acting agent's identifier (write attribution).

    Returns:
        A registry with the two brain tools appended, or the original
        registry unchanged when ``factory`` is ``None``.
    """
    if factory is None:
        return tool_registry
    brain_tools = factory.build_tools(
        project_id=NotBlankStr(project_id),
        author_agent_id=NotBlankStr(author_agent_id),
    )
    return ToolRegistry([*tool_registry.all_tools(), *brain_tools])


__all__ = ["registry_with_brain_tools"]
