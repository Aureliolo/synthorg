"""In-memory MCP installations repository for tests / no-persistence deployments.

Emits the same observability events as the durable backends so audit
logs are consistent regardless of which backend is wired.  Rows live
only for the lifetime of the running process; a persistence backend
is the source of truth in production.
"""

import asyncio
from typing import Final

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.integrations.mcp_catalog.installations import (
    McpInstallation,  # noqa: TC001
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    MCP_SERVER_INSTALLED,
    MCP_SERVER_UNINSTALLED,
)
from synthorg.observability.events.persistence import (
    PERSISTENCE_MCP_INSTALLATION_LIST_FAILED,
)
from synthorg.persistence._shared.pagination import validate_pagination_args

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 100


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
        _lock: Lazy-initialised ``asyncio.Lock`` that serialises every
            ``_store`` access. Created on first use (not in
            ``__init__``) so a fresh repo bound to a different event
            loop than the one that constructed it does not raise
            ``RuntimeError: <Lock> is bound to a different event loop``.
    """

    def __init__(self) -> None:
        self._store: dict[str, McpInstallation] = {}
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Return the per-instance lock, lazy-creating on first use."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def save(self, installation: McpInstallation) -> None:
        """Upsert an installation (by catalog_entry_id)."""
        async with self._get_lock():
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
        async with self._get_lock():
            return self._store.get(catalog_entry_id)

    async def list_items(
        self,
        *,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[McpInstallation, ...]:
        """List installations ordered by ``installed_at, catalog_entry_id`` ASC.

        Tiebreaker on ``catalog_entry_id`` matches the durable backends
        so the in-memory shim produces identical pagination windows for
        rows that share an ``installed_at`` instant.

        ``limit`` defaults to the protocol-wide pagination floor;
        callers needing more must loop with ``offset`` or pass a
        larger ``limit`` explicitly. Invalid inputs (``limit < 1``,
        ``offset < 0``, non-int, or ``bool``) raise ``QueryError`` to
        match the sqlite/postgres contract: silently coercing them
        would let bugs that the durable backends catch slip through
        in tests and no-persistence deployments.
        """
        validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_MCP_INSTALLATION_LIST_FAILED,
            backend="in_memory",
        )
        async with self._get_lock():
            snapshot = tuple(self._store.values())
        rows = tuple(
            sorted(
                snapshot,
                key=lambda i: (i.installed_at, i.catalog_entry_id),
            ),
        )
        return rows[offset : offset + limit]

    async def delete(self, catalog_entry_id: NotBlankStr) -> bool:
        """Delete by catalog entry id."""
        async with self._get_lock():
            removed = self._store.pop(catalog_entry_id, None) is not None
        if removed:
            logger.info(
                MCP_SERVER_UNINSTALLED,
                catalog_entry_id=catalog_entry_id,
                backend="in_memory",
            )
        return removed

    async def clear(self) -> int:
        """Drop every installation; return the number of rows removed.

        Used by tests between scenarios and by the dev-mode reset
        endpoint. Production deployments use the durable backends so
        this method is never reachable in serious environments.
        """
        async with self._get_lock():
            removed = len(self._store)
            self._store.clear()
        return removed

    async def size(self) -> int:
        """Return the count of installations currently held in memory."""
        async with self._get_lock():
            return len(self._store)
