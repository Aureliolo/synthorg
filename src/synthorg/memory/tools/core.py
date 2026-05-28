"""Core memory tools (read/write) for ToolRegistry integration."""

from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

from synthorg.core.enums import ToolCategory
from synthorg.memory.self_editing import (
    CORE_MEMORY_READ_TOOL,
    CORE_MEMORY_WRITE_TOOL,
    SelfEditingMemoryStrategy,
)
from synthorg.memory.self_editing_args import (
    CoreMemoryReadArgs,
    CoreMemoryWriteArgs,
)
from synthorg.memory.tools._shared import _is_error_response
from synthorg.tools.base import BaseTool, ToolExecutionResult

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr


class CoreMemoryReadTool(BaseTool):
    """``core_memory_read`` tool for ToolRegistry integration.

    Args:
        strategy: Self-editing strategy holding the backend.
        agent_id: Agent ID bound to this tool instance.
    """

    args_model: ClassVar[type[BaseModel] | None] = CoreMemoryReadArgs

    def __init__(
        self,
        *,
        strategy: SelfEditingMemoryStrategy,
        agent_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name=CORE_MEMORY_READ_TOOL,
            description=(
                "Read the current core memory block (persona, goals, "
                "key knowledge stored as SEMANTIC memories)."
            ),
            parameters_schema=CoreMemoryReadArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )
        self._strategy = strategy
        self._agent_id = agent_id

    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        """Execute a core memory read via the self-editing strategy.

        Args:
            arguments: Tool arguments from the LLM.

        Returns:
            ``ToolExecutionResult`` with formatted core entries or error.
        """
        result = await self._strategy.handle_tool_call(
            CORE_MEMORY_READ_TOOL, arguments, self._agent_id
        )
        return ToolExecutionResult(content=result, is_error=_is_error_response(result))


class CoreMemoryWriteTool(BaseTool):
    """``core_memory_write`` tool for ToolRegistry integration.

    Args:
        strategy: Self-editing strategy holding the backend.
        agent_id: Agent ID bound to this tool instance.
    """

    args_model: ClassVar[type[BaseModel] | None] = CoreMemoryWriteArgs

    def __init__(
        self,
        *,
        strategy: SelfEditingMemoryStrategy,
        agent_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name=CORE_MEMORY_WRITE_TOOL,
            description=(
                "Append an entry to core memory.  Core memory persists "
                "across sessions and is always injected into context."
            ),
            parameters_schema=CoreMemoryWriteArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )
        self._strategy = strategy
        self._agent_id = agent_id

    async def execute(self, *, arguments: dict[str, Any]) -> ToolExecutionResult:
        """Execute a core memory write via the self-editing strategy.

        Args:
            arguments: Tool arguments from the LLM (content).

        Returns:
            ``ToolExecutionResult`` with confirmation or error.
        """
        result = await self._strategy.handle_tool_call(
            CORE_MEMORY_WRITE_TOOL, arguments, self._agent_id
        )
        return ToolExecutionResult(content=result, is_error=_is_error_response(result))
