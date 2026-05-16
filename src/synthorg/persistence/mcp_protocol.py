"""MCP installation repository protocol.

Lives in persistence/ so the repository contract is colocated with
the other durable-state protocols.  Domain types (``McpInstallation``)
stay in ``synthorg.integrations.mcp_catalog``.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.integrations.mcp_catalog.installations import McpInstallation


@runtime_checkable
class McpInstallationRepository(
    IdKeyedRepository["McpInstallation", "NotBlankStr"],
    Protocol,
):
    """CRUD interface for MCP catalog installations.

    Composes :class:`IdKeyedRepository` (ADR-0001) with
    ``catalog_entry_id`` as the natural key. ``list_items`` is the
    only listing surface; no bespoke filtered query is needed.
    """

    async def save(self, entity: McpInstallation) -> None:
        """Upsert an installation (idempotent on catalog_entry_id)."""
        ...

    async def get(
        self,
        entity_id: NotBlankStr,
    ) -> McpInstallation | None:
        """Fetch an installation by catalog entry id."""
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[McpInstallation, ...]:
        """List recorded installations, optionally paginated.

        Implementations MUST return rows ordered by
        ``installed_at ASC, catalog_entry_id ASC`` so callers paging
        through ``offset/limit`` see a stable, deterministic window
        across calls and across backends.
        """
        ...

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete an installation.  Return ``True`` if a row was deleted."""
        ...
