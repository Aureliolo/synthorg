"""MCP installation repository protocol.

Lives in persistence/ so the repository contract is colocated with
the other durable-state protocols.  Domain types (``McpInstallation``)
stay in ``synthorg.integrations.mcp_catalog``.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.integrations.mcp_catalog.installations import McpInstallation


@runtime_checkable
class McpInstallationRepository(Protocol):
    """CRUD interface for MCP catalog installations."""

    async def save(self, installation: McpInstallation) -> None:
        """Upsert an installation (idempotent on catalog_entry_id)."""
        ...

    async def get(
        self,
        catalog_entry_id: NotBlankStr,
    ) -> McpInstallation | None:
        """Fetch an installation by catalog entry id."""
        ...

    async def list_all(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[McpInstallation, ...]:
        """List all recorded installations, optionally paginated.

        Implementations MUST return rows ordered by
        ``installed_at ASC, catalog_entry_id ASC`` so callers paging
        through ``offset/limit`` see a stable, deterministic window
        across calls and across backends.
        """
        ...

    async def delete(self, catalog_entry_id: NotBlankStr) -> bool:
        """Delete an installation.  Return ``True`` if a row was deleted."""
        ...
