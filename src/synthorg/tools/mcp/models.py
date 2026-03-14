"""MCP bridge internal value objects.

Defines ``MCPToolInfo`` for discovered tool metadata and
``MCPRawResult`` for raw MCP call results before mapping.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001


class MCPToolInfo(BaseModel):
    """Discovered tool metadata from an MCP server.

    Attributes:
        name: Tool name as reported by the server.
        description: Human-readable tool description.
        input_schema: JSON Schema for tool parameters.
        server_name: Name of the server that hosts this tool.
    """

    model_config = ConfigDict(frozen=True)

    name: NotBlankStr = Field(description="Tool name")
    description: str = Field(
        default="",
        description="Human-readable tool description",
    )
    input_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for tool parameters",
    )
    server_name: NotBlankStr = Field(
        description="Name of the hosting MCP server",
    )


class MCPRawResult(BaseModel):
    """Raw result from an MCP tool call before mapping.

    Attributes:
        content: MCP content blocks from the call result.
        is_error: Whether the MCP call reported an error.
        structured_content: Optional structured content from the result.
    """

    model_config = ConfigDict(frozen=True)

    content: tuple[Any, ...] = Field(
        default=(),
        description="MCP content blocks",
    )
    is_error: bool = Field(
        default=False,
        description="Whether the MCP call reported an error",
    )
    structured_content: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured content from the result",
    )
