"""In-memory MCP installations repository for tests / no-persistence deployments.

Emits the same observability events as the durable backends so audit
logs are consistent regardless of which backend is wired.  Rows live
only for the lifetime of the running process; a persistence backend
is the source of truth in production.
"""

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.integrations.mcp_catalog.installations import (
    McpInstallation,  # noqa: TC001
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    MCP_SERVER_INSTALLED,
    MCP_SERVER_UNINSTALLED,
)

logger = get_logger(__name__)


class InMemoryMcpInstallationRepository:
    """In-memory implementation of :class:`McpInstallationRepository`.

    Process-local fallback used when no persistence backend is
    configured (headless test apps, dev without a database).  Emits
    the same observability events as the durable backends so audit
    logs stay consistent regardless of which repository is wired.
    State is lost on process exit; a persistence backend is the source
    of truth in production.

    Attributes:
        _store: In-memory mapping from ``catalog_entry_id`` to
            :class:`McpInstallation`; the sole backing store for this
            repo implementation.
    """

    def __init__(self) -> None:
        self._store: dict[str, McpInstallation] = {}

    async def save(self, installation: McpInstallation) -> None:
        """Upsert an installation (by catalog_entry_id)."""
        self._store[installation.catalog_entry_id] = installation
        logger.info(
            MCP_SERVER_INSTALLED,
            catalog_entry_id=installation.catalog_entry_id,
            connection_name=installation.connection_name,
            backend="in_memory",
        )

    async def get(
        self,
        catalog_entry_id: NotBlankStr,
    ) -> McpInstallation | None:
        """Fetch by catalog entry id."""
        return self._store.get(catalog_entry_id)

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[McpInstallation, ...]:
        """List installations ordered by ``installed_at, catalog_entry_id`` ASC.

        Tiebreaker on ``catalog_entry_id`` matches the durable backends
        so the in-memory shim produces identical pagination windows for
        rows that share an ``installed_at`` instant.

        ``limit`` defaults to the protocol-wide pagination floor;
        callers needing more must loop with ``offset`` or pass a
        larger ``limit`` explicitly.
        """
        rows = tuple(
            sorted(
                self._store.values(),
                key=lambda i: (i.installed_at, i.catalog_entry_id),
            ),
        )
        effective_offset = max(0, int(offset))
        return rows[effective_offset : effective_offset + max(0, int(limit))]

    async def delete(self, catalog_entry_id: NotBlankStr) -> bool:
        """Delete by catalog entry id."""
        removed = self._store.pop(catalog_entry_id, None) is not None
        if removed:
            logger.info(
                MCP_SERVER_UNINSTALLED,
                catalog_entry_id=catalog_entry_id,
                backend="in_memory",
            )
        return removed
