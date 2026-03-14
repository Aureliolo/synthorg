"""Echo tool — returns the input message unchanged.

A minimal reference implementation of ``BaseTool`` useful for testing
and as a starting point for new tool implementations.
"""

from typing import Any

from synthorg.core.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult


class EchoTool(BaseTool):
    """Echoes the input message back as the tool result.

    Examples:
        Basic usage::

            tool = EchoTool()
            result = await tool.execute(arguments={"message": "hello"})
            assert result.content == "hello"
    """

    def __init__(self) -> None:
        """Initialize the echo tool with a fixed schema."""
        super().__init__(
            name="echo",
            description="Echoes the input message back",
            category=ToolCategory.OTHER,
            parameters_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
                "additionalProperties": False,
            },
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Return the ``message`` argument as content.

        Args:
            arguments: Must contain a ``message`` key with a string value.

        Returns:
            A ``ToolExecutionResult`` with the message as content.
        """
        return ToolExecutionResult(content=arguments["message"])
