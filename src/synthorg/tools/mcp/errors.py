"""MCP bridge error hierarchy.

All MCP errors extend :class:`~synthorg.tools.errors.ToolError`
and carry an immutable context mapping for structured metadata.
"""

from synthorg.tools.errors import ToolError


class MCPError(ToolError):
    """Base exception for MCP bridge errors."""


class MCPConnectionError(MCPError):
    """Failed to connect to an MCP server."""


class MCPClientUnrestartableError(MCPConnectionError):
    """The client is latched closed (e.g. after a disconnect timeout).

    A permanent, non-retryable ``MCPConnectionError``: reconnecting cannot
    succeed, so the retry handler must not back-off-and-retry it.
    """


class MCPTimeoutError(MCPError):
    """MCP operation timed out."""


class MCPDiscoveryError(MCPError):
    """Failed to discover tools from an MCP server."""


class MCPInvocationError(MCPError):
    """Failed to invoke an MCP tool."""
