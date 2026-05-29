"""Archival memory tools (search/write) for ToolRegistry integration."""

from typing import TYPE_CHECKING, Any, ClassVar, override

from pydantic import BaseModel

from synthorg.core.enums import ToolCategory
from synthorg.memory.self_editing import (
    ARCHIVAL_MEMORY_SEARCH_TOOL,
    ARCHIVAL_MEMORY_WRITE_TOOL,
    SelfEditingMemoryStrategy,
)
from synthorg.memory.self_editing_args import (
    ArchivalMemorySearchArgs,
    ArchivalMemoryWriteArgs,
)
from synthorg.memory.tools._shared import _is_error_response
from synthorg.tools.base import BaseTool, ToolExecutionResult

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr


class ArchivalMemorySearchTool(BaseTool):
    """``archival_memory_search`` tool for ToolRegistry integration.

    Args:
        strategy: Self-editing strategy holding the backend.
        agent_id: Agent ID bound to this tool instance.
    """

    args_model: ClassVar[type[BaseModel] | None] = ArchivalMemorySearchArgs

    def __init__(
        self,
        *,
        strategy: SelfEditingMemoryStrategy,
        agent_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name=ARCHIVAL_MEMORY_SEARCH_TOOL,
            description=(
                "Search archival memory by natural language query.  "
                "Archival memory is never auto-injected; use this tool "
                "to retrieve relevant past context on demand."
            ),
            parameters_schema=ArchivalMemorySearchArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )
        self._strategy = strategy
        self._agent_id = agent_id

    @override
    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        """Execute an archival memory search via the self-editing strategy.

        Args:
            arguments: Tool arguments from the LLM (query, category, limit).

        Returns:
            ``ToolExecutionResult`` with formatted entries or error.
        """
        result = await self._strategy.handle_tool_call(
            ARCHIVAL_MEMORY_SEARCH_TOOL, arguments, self._agent_id
        )
        return ToolExecutionResult(content=result, is_error=_is_error_response(result))


class ArchivalMemoryWriteTool(BaseTool):
    """``archival_memory_write`` tool for ToolRegistry integration.

    Args:
        strategy: Self-editing strategy holding the backend.
        agent_id: Agent ID bound to this tool instance.
    """

    args_model: ClassVar[type[BaseModel] | None] = ArchivalMemoryWriteArgs

    def __init__(
        self,
        *,
        strategy: SelfEditingMemoryStrategy,
        agent_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name=ARCHIVAL_MEMORY_WRITE_TOOL,
            description=(
                "Store a new entry in archival memory.  Use for facts, "
                "decisions, or events to retain for future retrieval."
            ),
            parameters_schema=ArchivalMemoryWriteArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )
        self._strategy = strategy
        self._agent_id = agent_id

    @override
    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        """Execute an archival memory write via the self-editing strategy.

        Args:
            arguments: Tool arguments from the LLM (content, category).

        Returns:
            ``ToolExecutionResult`` with confirmation or error.
        """
        result = await self._strategy.handle_tool_call(
            ARCHIVAL_MEMORY_WRITE_TOOL, arguments, self._agent_id
        )
        return ToolExecutionResult(content=result, is_error=_is_error_response(result))
