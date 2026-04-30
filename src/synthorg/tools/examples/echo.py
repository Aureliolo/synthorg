"""Echo tool -- returns the input message unchanged.

A minimal reference implementation of ``BaseTool`` useful for testing
and as a starting point for new tool implementations.

This tool follows the Phase 3 typed-args wiring (#1611): it sets
``args_model = EchoArgs`` so the ``ToolInvoker`` validates
``arguments`` via Pydantic before :meth:`execute` is called.
``parameters_schema`` is derived from the model's
``model_json_schema`` so the schema sent to the LLM stays in sync
automatically.
"""

from typing import Any, ClassVar

from pydantic import BaseModel  # noqa: TC002 -- ClassVar type at runtime

from synthorg.core.enums import ToolCategory
from synthorg.tools._misc_args import EchoArgs
from synthorg.tools.base import BaseTool, ToolExecutionResult


class EchoTool(BaseTool):
    """Echoes the input message back as the tool result.

    Examples:
        Basic usage::

            tool = EchoTool()
            result = await tool.execute(arguments={"message": "hello"})
            assert result.content == "hello"
    """

    args_model: ClassVar[type[BaseModel] | None] = EchoArgs

    def __init__(self) -> None:
        """Initialize the echo tool, deriving its schema from EchoArgs."""
        super().__init__(
            name="echo",
            description="Echoes the input message back",
            category=ToolCategory.OTHER,
            parameters_schema=EchoArgs.model_json_schema(),
        )

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Return the ``message`` argument as content.

        ``ToolInvoker`` has already validated ``arguments`` against
        :class:`EchoArgs` via :attr:`BaseTool.args_model` before this
        method is called, so the access below is unconditionally safe.
        """
        return ToolExecutionResult(content=arguments["message"])
