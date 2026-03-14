"""MCP bridge configuration models.

Defines ``MCPServerConfig`` for individual MCP server connections and
``MCPConfig`` as the top-level container. Both are frozen Pydantic
models following the project's immutability conventions.
"""

from collections import Counter
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_CONFIG_VALIDATION_FAILED,
)

logger = get_logger(__name__)


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server connection.

    Attributes:
        name: Unique server identifier.
        transport: Transport type (``"stdio"`` or ``"streamable_http"``).
        command: Command to launch a stdio server.
        args: Command-line arguments for stdio server.
        env: Environment variables for stdio server.
        url: URL for streamable HTTP server.
        headers: HTTP headers for streamable HTTP server.
        enabled_tools: Allowlist of tool names (``None`` = all).
        disabled_tools: Denylist of tool names.
        timeout_seconds: Timeout for tool invocations.
        connect_timeout_seconds: Timeout for initial connection.
        result_cache_ttl_seconds: TTL for result cache entries.
        result_cache_max_size: Maximum result cache entries.
        enabled: Whether the server is active.
    """

    model_config = ConfigDict(frozen=True)

    name: NotBlankStr = Field(description="Unique server identifier")
    transport: Literal["stdio", "streamable_http"] = Field(
        description="Transport type: stdio or streamable_http",
    )
    # stdio fields
    command: NotBlankStr | None = Field(
        default=None,
        description="Command to launch a stdio server",
    )
    args: tuple[str, ...] = Field(
        default=(),
        description="Command-line arguments for stdio server",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables for stdio server",
    )
    # streamable_http fields
    url: NotBlankStr | None = Field(
        default=None,
        description="URL for streamable HTTP server",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="HTTP headers for streamable HTTP server",
    )
    # Common
    enabled_tools: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="Allowlist of tool names (None = all)",
    )
    disabled_tools: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Denylist of tool names",
    )
    timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=600,
        description="Timeout for tool invocations in seconds",
    )
    connect_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
        description="Timeout for initial connection in seconds",
    )
    result_cache_ttl_seconds: float = Field(
        default=60.0,
        ge=0,
        description="TTL for result cache entries in seconds",
    )
    result_cache_max_size: int = Field(
        default=256,
        ge=0,
        description="Maximum result cache entries",
    )
    enabled: bool = Field(
        default=True,
        description="Whether the server is active",
    )

    @model_validator(mode="after")
    def _validate_transport_fields(self) -> Self:
        """Validate transport-specific required fields.

        Stdio transport requires ``command``; streamable_http requires
        ``url``.
        """
        if self.transport == "stdio" and self.command is None:
            msg = f"Server {self.name!r}: stdio transport requires 'command'"
            logger.warning(
                MCP_CONFIG_VALIDATION_FAILED,
                server=self.name,
                reason=msg,
            )
            raise ValueError(msg)
        if self.transport == "streamable_http" and self.url is None:
            msg = f"Server {self.name!r}: streamable_http transport requires 'url'"
            logger.warning(
                MCP_CONFIG_VALIDATION_FAILED,
                server=self.name,
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_tool_filters(self) -> Self:
        """Ensure enabled_tools and disabled_tools do not overlap."""
        if self.enabled_tools is not None and self.disabled_tools:
            overlap = set(self.enabled_tools) & set(self.disabled_tools)
            if overlap:
                msg = (
                    f"Server {self.name!r}: enabled_tools and "
                    f"disabled_tools overlap: {sorted(overlap)}"
                )
                logger.warning(
                    MCP_CONFIG_VALIDATION_FAILED,
                    server=self.name,
                    reason=msg,
                )
                raise ValueError(msg)
        return self


class MCPConfig(BaseModel):
    """Top-level MCP bridge configuration.

    Attributes:
        servers: Tuple of MCP server configurations.
    """

    model_config = ConfigDict(frozen=True)

    servers: tuple[MCPServerConfig, ...] = Field(
        default=(),
        description="MCP server configurations",
    )

    @model_validator(mode="after")
    def _validate_unique_server_names(self) -> Self:
        """Ensure server names are unique."""
        names = [s.name for s in self.servers]
        if len(names) != len(set(names)):
            dupes = sorted(n for n, c in Counter(names).items() if c > 1)
            msg = f"Duplicate MCP server names: {dupes}"
            logger.warning(
                MCP_CONFIG_VALIDATION_FAILED,
                reason=msg,
            )
            raise ValueError(msg)
        return self
