"""MCP bridge internal value objects.

Defines ``MCPToolInfo`` for discovered tool metadata and
``MCPRawResult`` for raw MCP call results before mapping.
"""

import copy
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from synthorg.core.types import NotBlankStr

#: A tool name reaches a Prometheus label and a registry key, so it is bounded
#: at the boundary the server's ``tools/list`` crosses rather than downstream.
#: The server chooses these names, and a hostile or flapping one that returns
#: fresh long names on every reconnect would otherwise mint an unbounded number
#: of permanent metric series. Generous next to any real tool name.
_MAX_TOOL_NAME_LENGTH: Final[int] = 128


class MCPToolInfo(BaseModel):
    """Discovered tool metadata from an MCP server.

    Attributes:
        name: Tool name as reported by the server.
        description: Human-readable tool description.
        input_schema: JSON Schema for tool parameters.
        server_name: Name of the server that hosts this tool.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(
        description="Tool name",
        max_length=_MAX_TOOL_NAME_LENGTH,
    )
    description: str = Field(
        default="",
        description="Human-readable tool description",
    )
    input_schema: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="JSON Schema for tool parameters",
    )
    server_name: NotBlankStr = Field(
        description="Name of the hosting MCP server",
    )

    @model_validator(mode="after")
    def _deep_copy_input_schema(self) -> Self:
        """Deep-copy ``input_schema`` so the frozen model cannot be aliased.

        Returns:
            The instance with ``input_schema`` deep-copied.
        """
        object.__setattr__(self, "input_schema", copy.deepcopy(self.input_schema))
        return self


class MCPServerStatus(BaseModel):
    """Per-server outcome of an MCP factory connect pass.

    Surfaced by ``MCPToolFactory.server_statuses`` so a caller (health
    endpoint, dashboard) can tell a dropped server from a connectionless
    one, rather than inferring failure only from an empty tool tuple.

    Attributes:
        name: Configured server name.
        connected: Whether connect + discovery succeeded.
        tool_count: Number of tools discovered (0 when not connected).
        error: Scrubbed failure description when not connected.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Configured server name")
    connected: bool = Field(description="Whether connect + discovery succeeded")
    tool_count: int = Field(default=0, ge=0, description="Discovered tool count")
    error: str | None = Field(
        default=None,
        description="Scrubbed failure description when not connected",
    )


class MCPRawResult(BaseModel):
    """Raw result from an MCP tool call before mapping.

    Attributes:
        content: MCP content blocks from the call result.
        is_error: Whether the MCP call reported an error.
        structured_content: Optional structured content from the result.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    content: tuple[object, ...] = Field(
        default=(),
        description="MCP content blocks",
    )
    is_error: bool = Field(
        default=False,
        description="Whether the MCP call reported an error",
    )
    structured_content: dict[str, JsonValue] | None = Field(
        default=None,
        description="Optional structured content from the result",
    )
