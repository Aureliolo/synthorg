"""MCP bridge configuration models.

Defines ``MCPServerConfig`` for individual MCP server connections and
``MCPConfig`` as the top-level container. Both are frozen Pydantic
models following the project's immutability conventions.
"""

import ipaddress
from collections import Counter
from typing import Final, Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_CONFIG_VALIDATION_FAILED,
)

logger = get_logger(__name__)

_ALLOWED_URL_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
"""Schemes permitted for a ``streamable_http`` server URL.

SSRF guard: a server URL can arrive from a catalog installation, so
``file://`` / ``ftp://`` / ``gopher://`` and friends are rejected."""


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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
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
    def _validate_url_scheme(self) -> Self:
        """Restrict a streamable_http URL to http(s) and block metadata IPs.

        SSRF guard for catalog-installed servers: the scheme must be in
        :data:`_ALLOWED_URL_SCHEMES`, and a link-local host (the
        ``169.254.0.0/16`` / ``fe80::/10`` cloud-metadata range) is
        rejected. Private / internal addresses are intentionally allowed
        -- self-hosted internal MCP servers are a first-class deployment.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If the scheme is disallowed or the host is link-local.
        """
        if self.transport != "streamable_http" or self.url is None:
            return self
        parsed = urlparse(self.url)
        if parsed.scheme not in _ALLOWED_URL_SCHEMES:
            msg = (
                f"Server {self.name!r}: url scheme {parsed.scheme!r} is not "
                f"allowed (must be one of {sorted(_ALLOWED_URL_SCHEMES)})"
            )
            logger.warning(
                MCP_CONFIG_VALIDATION_FAILED,
                server=self.name,
                reason=msg,
            )
            raise ValueError(msg)
        host = parsed.hostname or ""
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None and ip.is_link_local:
            msg = (
                f"Server {self.name!r}: url host {host!r} is in the link-local "
                f"cloud-metadata range and is blocked"
            )
            logger.warning(
                MCP_CONFIG_VALIDATION_FAILED,
                server=self.name,
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_tool_filters(self) -> Self:
        """Ensure enabled_tools and disabled_tools do not overlap.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    servers: tuple[MCPServerConfig, ...] = Field(
        default=(),
        description="MCP server configurations",
    )

    @model_validator(mode="after")
    def _validate_unique_server_names(self) -> Self:
        """Ensure server names are unique.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
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
