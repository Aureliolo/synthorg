"""Tool registry — maps tool names to ``BaseTool`` instances.

Immutable after construction.  Provides lookup, membership testing,
and conversion to a tuple of ``ToolDefinition`` objects for LLM providers.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_NOT_FOUND,
    TOOL_REGISTRY_BUILT,
    TOOL_REGISTRY_CONTAINS_TYPE_ERROR,
    TOOL_REGISTRY_DUPLICATE,
)

from .errors import ToolNotFoundError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from synthorg.providers.models import ToolDefinition

    from .base import BaseTool

logger = get_logger(__name__)


class ToolRegistry:
    """Immutable registry of named tools.

    Examples:
        Build from a list of tools::

            registry = ToolRegistry([echo_tool, search_tool])
            tool = registry.get("echo")

        Check membership::

            if "echo" in registry:
                ...
    """

    def __init__(self, tools: Iterable[BaseTool]) -> None:
        """Initialize with an iterable of tools.

        Args:
            tools: Tools to register. Duplicate names raise ``ValueError``.

        Raises:
            ValueError: If two tools share the same name.
        """
        mapping: dict[str, BaseTool] = {}
        for tool in tools:
            if tool.name in mapping:
                logger.warning(
                    TOOL_REGISTRY_DUPLICATE,
                    tool_name=tool.name,
                )
                msg = f"Duplicate tool name: {tool.name!r}"
                raise ValueError(msg)
            mapping[tool.name] = tool
        self._tools: MappingProxyType[str, BaseTool] = MappingProxyType(mapping)
        logger.info(
            TOOL_REGISTRY_BUILT,
            tool_count=len(self._tools),
            tools=sorted(self._tools),
        )

    def get(self, name: str) -> BaseTool:
        """Look up a tool by name.

        Args:
            name: Tool name.

        Returns:
            The registered tool instance.

        Raises:
            ToolNotFoundError: If no tool is registered with that name.
        """
        tool = self._tools.get(name)
        if tool is None:
            available = sorted(self._tools) or ["(none)"]
            logger.warning(
                TOOL_NOT_FOUND,
                tool_name=name,
                available=available,
            )
            msg = (
                f"Tool {name!r} is not registered. "
                f"Available tools: {', '.join(available)}"
            )
            raise ToolNotFoundError(msg, context={"tool": name})
        return tool

    def list_tools(self) -> tuple[str, ...]:
        """Return sorted tuple of registered tool names."""
        return tuple(sorted(self._tools))

    def all_tools(self) -> tuple[BaseTool, ...]:
        """Return all registered tool instances, sorted by name."""
        return tuple(self._tools[name] for name in sorted(self._tools))

    def to_definitions(self) -> tuple[ToolDefinition, ...]:
        """Return all tool definitions as a sorted tuple, ordered by name.

        Returns:
            Sorted tuple of tool definitions for LLM providers.
        """
        return tuple(self._tools[name].to_definition() for name in sorted(self._tools))

    def __contains__(self, name: object) -> bool:
        """Check whether a tool name is registered."""
        if not isinstance(name, str):
            logger.debug(
                TOOL_REGISTRY_CONTAINS_TYPE_ERROR,
                name_type=type(name).__name__,
            )
            return False
        return name in self._tools

    def __len__(self) -> int:
        """Return the number of registered tools."""
        return len(self._tools)
