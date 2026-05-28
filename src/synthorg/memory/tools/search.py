"""``SearchMemoryTool`` for ToolRegistry integration."""

from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

from synthorg.core.enums import ToolCategory
from synthorg.memory.tool_retriever import (
    SEARCH_MEMORY_TOOL_NAME,
    ToolBasedInjectionStrategy,
)
from synthorg.memory.tools._args import SearchMemoryArgs
from synthorg.memory.tools._shared import _is_error_response
from synthorg.tools.base import BaseTool, ToolExecutionResult

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr


class SearchMemoryTool(BaseTool):
    """``search_memory`` tool for ToolRegistry integration.

    Delegates execution to ``ToolBasedInjectionStrategy.handle_tool_call``,
    wrapping the string result in a ``ToolExecutionResult``.  The
    ``agent_id`` is bound at construction time (tools are per-agent).

    Args:
        strategy: The tool-based injection strategy holding the backend.
        agent_id: Agent ID bound to this tool instance.
    """

    args_model: ClassVar[type[BaseModel] | None] = SearchMemoryArgs

    def __init__(
        self,
        *,
        strategy: ToolBasedInjectionStrategy,
        agent_id: NotBlankStr,
    ) -> None:
        super().__init__(
            name=SEARCH_MEMORY_TOOL_NAME,
            description=(
                "Search agent memory for relevant past context, "
                "decisions, or learned information."
            ),
            parameters_schema=SearchMemoryArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )
        self._strategy = strategy
        self._agent_id = agent_id

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Execute a memory search via the injection strategy.

        Args:
            arguments: Tool arguments from the LLM, validated against
                :class:`SearchMemoryArgs` at the invoker boundary.

        Returns:
            ``ToolExecutionResult`` with formatted memory entries or error.
        """
        result = await self._strategy.handle_tool_call(
            SEARCH_MEMORY_TOOL_NAME,
            arguments,
            self._agent_id,
        )
        return ToolExecutionResult(
            content=result,
            is_error=_is_error_response(result),
        )
