"""Tool registry -- maps tool names to ``BaseTool`` instances.

Immutable after construction.  Provides lookup, membership testing,
and conversion to a tuple of ``ToolDefinition`` objects for LLM providers.
"""

import difflib
from collections.abc import Iterable
from types import MappingProxyType
from typing import Final

from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.core.tool_disclosure import ToolL1Metadata
from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_NOT_FOUND,
    TOOL_REGISTRY_BUILT,
    TOOL_REGISTRY_CONTAINS_TYPE_ERROR,
    TOOL_REGISTRY_DUPLICATE,
)
from synthorg.providers.models import ToolDefinition

from .base import BaseTool
from .errors import ToolNotFoundError

logger = get_logger(__name__)

#: Enough to disambiguate a namespaced miss without becoming another wall
#: of names; the whole point is that the list is short enough to read.
_MAX_SUGGESTIONS: Final[int] = 4


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
            suggestions = self._nearest(name)
            logger.warning(
                TOOL_NOT_FOUND,
                tool_name=name,
                suggestions=suggestions,
                available=available,
            )
            # Lead with the near-matches. A live run lost an entire task to
            # this: an agent called 'search' eleven times, was handed all
            # forty registered names each time, never connected it to
            # 'memory.search', and burned its whole turn budget. A wall of
            # names is not an answer to "which one did I mean".
            hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            msg = (
                f"Tool {name!r} is not registered.{hint} "
                f"Available tools: {', '.join(available)}"
            )
            raise ToolNotFoundError(msg, context={"tool": name})
        return tool

    def _nearest(self, name: str) -> tuple[str, ...]:
        """Registered names a caller asking for *name* most likely meant.

        Namespaced tools are the common miss (``search`` for
        ``memory.search``), and those score poorly on edit distance because
        the prefix dominates, so a segment/substring match is taken first
        and edit distance only fills the gap.

        Args:
            name: The name that was not found.

        Returns:
            Up to ``_MAX_SUGGESTIONS`` candidates, best first, or empty when
            nothing resembles *name*.
        """
        wanted = normalize_ascii_lowercase(name)
        if not wanted:
            return ()
        ranked: list[str] = [
            candidate
            for candidate in sorted(self._tools)
            if wanted in normalize_ascii_lowercase(candidate).split(".")
            or wanted in normalize_ascii_lowercase(candidate)
        ]
        for candidate in difflib.get_close_matches(
            wanted, sorted(self._tools), n=_MAX_SUGGESTIONS
        ):
            if candidate not in ranked:
                ranked.append(candidate)
        return tuple(ranked[:_MAX_SUGGESTIONS])

    def list_tools(self) -> tuple[str, ...]:
        """Return sorted tuple of registered tool names.

        Returns:
            Tuple of ``str``.
        """
        return tuple(sorted(self._tools))

    def all_tools(self) -> tuple[BaseTool, ...]:
        """Return all registered tool instances, sorted by name.

        Returns:
            Tuple of ``BaseTool``.
        """
        return tuple(self._tools[name] for name in sorted(self._tools))

    def to_definitions(self) -> tuple[ToolDefinition, ...]:
        """Return all tool definitions as a sorted tuple, ordered by name.

        Returns:
            Sorted tuple of tool definitions for LLM providers.
        """
        return tuple(self._tools[name].to_definition() for name in sorted(self._tools))

    def __contains__(self, name: object) -> bool:
        """Check whether a tool name is registered.

        Returns:
            ``True`` if the operation succeeds, ``False`` otherwise.
        """
        if not isinstance(name, str):
            logger.debug(
                TOOL_REGISTRY_CONTAINS_TYPE_ERROR,
                name_type=type(name).__name__,
            )
            return False
        return name in self._tools

    def to_l1_summaries(self) -> tuple[ToolL1Metadata, ...]:
        """Return L1 metadata for all tools, sorted by name.

        Lightweight extraction for system prompt injection.  Does
        not include L2 bodies or L3 resources.

        Returns:
            Sorted tuple of L1 metadata.
        """
        return tuple(self._tools[name].to_l1_metadata() for name in sorted(self._tools))

    def __len__(self) -> int:
        """Return the number of registered tools.

        Returns:
            Result of type ``int``.
        """
        return len(self._tools)
