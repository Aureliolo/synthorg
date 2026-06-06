# module-kind: declarative
"""Persistence event constants for the mcp_installation sub-domain."""

from typing import Final

PERSISTENCE_MCP_INSTALLATION_SAVE_FAILED: Final[str] = (
    "persistence.mcp_installation.save_failed"
)
PERSISTENCE_MCP_INSTALLATION_DELETE_FAILED: Final[str] = (
    "persistence.mcp_installation.delete_failed"
)
PERSISTENCE_MCP_INSTALLATION_LIST_FAILED: Final[str] = (
    "persistence.mcp_installation.list_failed"
)
