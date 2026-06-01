"""Echo tool -- returns the input message unchanged.

A minimal reference implementation of ``BaseTool`` useful for testing
and as a starting point for new tool implementations.

It sets ``args_model = EchoArgs`` so the ``ToolInvoker`` validates
``arguments`` via Pydantic before :meth:`execute` is called.
``parameters_schema`` is derived from the model's
``model_json_schema`` so the schema sent to the LLM stays in sync
automatically.
"""

from typing import ClassVar, cast, override

from pydantic import BaseModel, JsonValue

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

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, JsonValue],
    ) -> ToolExecutionResult:
        """Return the ``message`` argument as content.

        ``ToolInvoker`` has already validated ``arguments`` against
        :class:`EchoArgs` via :attr:`BaseTool.args_model` before this
        method is called, so the access below is unconditionally safe.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        return ToolExecutionResult(content=cast("str", arguments["message"]))
