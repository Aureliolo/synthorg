"""Recall memory tools (read/write) for ToolRegistry integration."""

from typing import TYPE_CHECKING, Any, ClassVar, override

from pydantic import BaseModel

from synthorg.core.enums import ToolCategory
from synthorg.memory.self_editing import (
    RECALL_MEMORY_READ_TOOL,
    RECALL_MEMORY_WRITE_TOOL,
    SelfEditingMemoryStrategy,
)
from synthorg.memory.self_editing_args import (
    RecallMemoryReadArgs,
    RecallMemoryWriteArgs,
)
from synthorg.memory.tools._shared import _is_error_response
from synthorg.tools.base import BaseTool, ToolExecutionResult

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr


class RecallMemoryReadTool(BaseTool):
    """``recall_memory_read`` tool for ToolRegistry integration.

    Args:
        strategy: Self-editing strategy holding the backend.
        agent_id: Agent ID bound to this tool instance.
    """

    args_model: ClassVar[type[BaseModel] | None] = RecallMemoryReadArgs

    def __init__(
        self,
        *,
        strategy: SelfEditingMemoryStrategy,
        agent_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name=RECALL_MEMORY_READ_TOOL,
            description=(
                "Retrieve a specific episodic memory by its ID.  "
                "Use the ID returned by recall_memory_write."
            ),
            parameters_schema=RecallMemoryReadArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )
        self._strategy = strategy
        self._agent_id = agent_id

    @override
    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        """Execute a recall memory read via the self-editing strategy.

        Args:
            arguments: Tool arguments from the LLM (memory_id).

        Returns:
            ``ToolExecutionResult`` with the entry or error.
        """
        result = await self._strategy.handle_tool_call(
            RECALL_MEMORY_READ_TOOL, arguments, self._agent_id
        )
        return ToolExecutionResult(content=result, is_error=_is_error_response(result))


class RecallMemoryWriteTool(BaseTool):
    """``recall_memory_write`` tool for ToolRegistry integration.

    Args:
        strategy: Self-editing strategy holding the backend.
        agent_id: Agent ID bound to this tool instance.
    """

    args_model: ClassVar[type[BaseModel] | None] = RecallMemoryWriteArgs

    def __init__(
        self,
        *,
        strategy: SelfEditingMemoryStrategy,
        agent_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name=RECALL_MEMORY_WRITE_TOOL,
            description=(
                "Record an episodic event or experience.  Returns the "
                "memory ID for future retrieval via recall_memory_read."
            ),
            parameters_schema=RecallMemoryWriteArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )
        self._strategy = strategy
        self._agent_id = agent_id

    @override
    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        """Execute a recall memory write via the self-editing strategy.

        Args:
            arguments: Tool arguments from the LLM (content).

        Returns:
            ``ToolExecutionResult`` with the new memory ID or error.
        """
        result = await self._strategy.handle_tool_call(
            RECALL_MEMORY_WRITE_TOOL, arguments, self._agent_id
        )
        return ToolExecutionResult(content=result, is_error=_is_error_response(result))
