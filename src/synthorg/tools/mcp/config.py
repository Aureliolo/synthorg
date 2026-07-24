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

from synthorg.core.env_var_safety import validate_credential_env_var_name
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_CONFIG_VALIDATION_FAILED,
)
from synthorg.observability.redaction import safe_error_description
from synthorg.tools.mcp._npm_pin import unpinned_npm_packages

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
        connection_name: Bound connection whose credentials are injected at
            spawn (never persisted here); ``None`` for connectionless servers.
        credential_env_map: Map of connection credential field name to the
            environment variable the server reads it from (e.g.
            ``{"token": "GITHUB_PERSONAL_ACCESS_TOKEN"}``). Credentials are
            forwarded only by environment variable so a secret value can never
            land in the spawned process argv (visible via ``ps``/``/proc``).
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
    connection_name: NotBlankStr | None = Field(
        default=None,
        description="Bound connection whose credentials are injected at spawn",
    )
    credential_env_map: dict[str, str] = Field(
        default_factory=dict,
        description="Credential field name to environment variable name",
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
    def _validate_npm_pin(self) -> Self:
        """Reject an ``npx``-launched stdio server with an unpinned package.

        The catalog installer pins every package to ``@<version>``, but a
        hand-authored ``MCPServerConfig`` bypasses that path. An unpinned
        (or ``@latest``) package resolves to whatever is newest on every
        reconnect, so an un-reviewed version could start running under the
        agent's tools without any change to the config. Requiring a
        concrete pin closes that supply-chain gap at the model boundary.

        Returns:
            The unchanged model when the command is not npx-style or its
            package is already pinned.

        Raises:
            ValueError: If the command runs an unpinned npm package.
        """
        if self.transport != "stdio" or self.command is None:
            return self
        unpinned = unpinned_npm_packages(str(self.command), self.args)
        if not unpinned:
            return self
        rendered = ", ".join(repr(spec) for spec in unpinned)
        msg = (
            f"Server {self.name!r}: npm package(s) {rendered} must be pinned to "
            f"an explicit version (e.g. '{unpinned[0]}@1.2.3'), not "
            f"floating/unpinned"
        )
        logger.warning(
            MCP_CONFIG_VALIDATION_FAILED,
            server=self.name,
            reason=msg,
        )
        raise ValueError(msg)

    @model_validator(mode="after")
    def _validate_credential_binding_is_stdio(self) -> Self:
        """Confine credential injection to the stdio transport.

        The ``streamable_http`` connect path (``_connect_http``) ignores
        ``connection_name`` / ``credential_env_map`` entirely, so binding
        them on a non-stdio server is silently ineffective; reject it at
        construction rather than let an operator believe a remote server is
        authenticated when it never receives the credential.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If credential binding is set on a non-stdio transport,
                or a credential map is declared without a bound connection.
        """
        if self.connection_name is None and self.credential_env_map:
            # A map with no connection has nothing to resolve from; the client's
            # stdio launch would silently skip it, leaving a credential-required
            # server spawned unauthenticated. Reject at construction instead.
            msg = (
                f"Server {self.name!r}: credential_env_map requires a bound "
                f"connection_name"
            )
            logger.warning(
                MCP_CONFIG_VALIDATION_FAILED,
                server=self.name,
                reason=msg,
            )
            raise ValueError(msg)
        if self.transport == "stdio":
            return self
        if self.connection_name is not None or self.credential_env_map:
            msg = (
                f"Server {self.name!r}: credential binding "
                f"(connection_name/credential_env_map) is only supported on the "
                f"stdio transport, not {self.transport!r}"
            )
            logger.warning(
                MCP_CONFIG_VALIDATION_FAILED,
                server=self.name,
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_credential_env_var_names(self) -> Self:
        """Screen credential target env-var names at the injection boundary.

        ``_resolve_stdio_launch`` injects each credential value into
        ``env[<name>]`` for the spawned child, so a hostile or careless
        ``credential_env_map`` value could aim a secret at a loader/process
        control variable (``LD_PRELOAD``, ``NODE_OPTIONS``, ``PATH``) and steer
        the child. This screens the config regardless of whether it came from a
        catalog install or a hand-authored YAML block.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If a target env-var name is malformed or dangerous.
        """
        for env_var in self.credential_env_map.values():
            try:
                validate_credential_env_var_name(env_var)
            except ValueError as exc:
                logger.warning(
                    MCP_CONFIG_VALIDATION_FAILED,
                    server=self.name,
                    reason="credential_env_map target env-var name rejected",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
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
