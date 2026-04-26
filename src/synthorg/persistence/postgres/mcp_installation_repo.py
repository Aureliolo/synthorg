"""Postgres-backed MCP installations repository.

Persists :class:`McpInstallation` rows in the ``mcp_installations``
table using the shared ``AsyncConnectionPool``.  Each operation
checks out a connection via ``async with pool.connection() as conn``;
the context manager auto-commits on clean exit.

Read paths use ``psycopg.rows.dict_row`` so row access is by column
name -- robust to accidental SELECT re-ordering.
"""

from typing import TYPE_CHECKING, Any

from psycopg.rows import dict_row

from synthorg.core.types import NotBlankStr
from synthorg.integrations.mcp_catalog.installations import McpInstallation
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    MCP_SERVER_INSTALL_FAILED,
    MCP_SERVER_INSTALLED,
    MCP_SERVER_UNINSTALLED,
)
from synthorg.persistence._shared import coerce_row_timestamp, normalize_utc

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


logger = get_logger(__name__)


def _row_to_installation(row: dict[str, Any]) -> McpInstallation:
    """Deserialize a dict row into an :class:`McpInstallation`."""
    connection_name_raw = row["connection_name"]
    return McpInstallation(
        catalog_entry_id=NotBlankStr(row["catalog_entry_id"]),
        connection_name=(
            NotBlankStr(connection_name_raw) if connection_name_raw else None
        ),
        installed_at=coerce_row_timestamp(row["installed_at"]),
    )


class PostgresMcpInstallationRepository:
    """Postgres implementation of :class:`McpInstallationRepository`."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, installation: McpInstallation) -> None:
        """Upsert an installation row (idempotent on catalog_entry_id)."""
        installed_at = normalize_utc(installation.installed_at)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO mcp_installations (
                        catalog_entry_id, connection_name, installed_at
                    ) VALUES (%s, %s, %s)
                    ON CONFLICT (catalog_entry_id) DO UPDATE SET
                        connection_name = EXCLUDED.connection_name,
                        installed_at = EXCLUDED.installed_at
                    """,
                    (
                        installation.catalog_entry_id,
                        installation.connection_name,
                        installed_at,
                    ),
                )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                MCP_SERVER_INSTALL_FAILED,
                operation="upsert",
                catalog_entry_id=installation.catalog_entry_id,
                connection_name=installation.connection_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                backend="postgres",
            )
            raise
        logger.info(
            MCP_SERVER_INSTALLED,
            catalog_entry_id=installation.catalog_entry_id,
            connection_name=installation.connection_name,
            backend="postgres",
        )

    async def get(
        self,
        catalog_entry_id: NotBlankStr,
    ) -> McpInstallation | None:
        """Fetch a single installation by catalog entry id."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    """
                    SELECT catalog_entry_id, connection_name, installed_at
                    FROM mcp_installations
                    WHERE catalog_entry_id = %s
                    """,
                    (catalog_entry_id,),
                )
                row = await cur.fetchone()
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                MCP_SERVER_INSTALL_FAILED,
                operation="get",
                catalog_entry_id=catalog_entry_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                backend="postgres",
            )
            raise
        if row is None:
            return None
        return _row_to_installation(row)

    async def list_all(self) -> tuple[McpInstallation, ...]:
        """List all recorded installations in a deterministic order.

        Sorted by ``installed_at`` ascending with ``catalog_entry_id``
        as a stable tiebreaker so rows with identical timestamps
        (restores, backfills, clock skew) are always returned in the
        same order across calls.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    """
                    SELECT catalog_entry_id, connection_name, installed_at
                    FROM mcp_installations
                    ORDER BY installed_at ASC, catalog_entry_id ASC
                    """,
                )
                rows = await cur.fetchall()
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                MCP_SERVER_INSTALL_FAILED,
                operation="list_all",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                backend="postgres",
            )
            raise
        return tuple(_row_to_installation(row) for row in rows)

    async def delete(self, catalog_entry_id: NotBlankStr) -> bool:
        """Delete an installation.  Returns ``True`` if a row was removed."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM mcp_installations WHERE catalog_entry_id = %s",
                    (catalog_entry_id,),
                )
                deleted = cur.rowcount > 0
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                MCP_SERVER_INSTALL_FAILED,
                operation="delete",
                catalog_entry_id=catalog_entry_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                backend="postgres",
            )
            raise
        if deleted:
            logger.info(
                MCP_SERVER_UNINSTALLED,
                catalog_entry_id=catalog_entry_id,
                backend="postgres",
            )
        return deleted
