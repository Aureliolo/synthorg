"""Postgres-backed MCP installations repository.

Persists :class:`McpInstallation` rows in the ``mcp_installations``
table using the shared ``AsyncConnectionPool``.  Each operation
checks out a connection via ``async with pool.connection() as conn``;
the context manager auto-commits on clean exit.

Read paths use ``psycopg.rows.dict_row`` so row access is by column
name -- robust to accidental SELECT re-ordering.
"""

from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.mcp_catalog.installations import McpInstallation
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    MCP_SERVER_INSTALL_FAILED,
    MCP_SERVER_INSTALLED,
    MCP_SERVER_UNINSTALLED,
)
from synthorg.observability.events.persistence import (
    PERSISTENCE_MCP_INSTALLATION_LIST_FAILED,
)
from synthorg.persistence._shared import (
    DEFAULT_LIST_LIMIT,
    coerce_row_timestamp,
    normalize_utc,
)
from synthorg.persistence._shared.pagination import validate_pagination_args

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


logger = get_logger(__name__)


def _row_to_installation(row: dict[str, Any]) -> McpInstallation:
    """Deserialize a dict row into an :class:`McpInstallation`.

    Returns:
        Result of type ``McpInstallation``.
    """
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
        """Upsert an installation row (idempotent on catalog_entry_id).

        Raises:
            ConstraintViolationError: When the upsert violates a
                database constraint, in practice the foreign key on
                ``connection_name`` when the supplied connection has
                not been persisted. Surfaces the constraint identity
                so the central exception handler can return a
                structured 4xx envelope.
            QueryError: For all other ``psycopg`` failures (pool
                checkout errors, transient connectivity, etc.). Parity
                with the SQLite backend so callers receive a uniform
                envelope across backends.
        """
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
        except psycopg.errors.IntegrityError as exc:
            constraint = (
                getattr(getattr(exc, "diag", None), "constraint_name", None)
                or "<unknown>"
            )
            logger.warning(
                MCP_SERVER_INSTALL_FAILED,
                operation="upsert",
                catalog_entry_id=installation.catalog_entry_id,
                connection_name=installation.connection_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                constraint=constraint,
                backend="postgres",
            )
            msg = (
                f"Constraint violation saving MCP installation "
                f"{installation.catalog_entry_id!r}"
            )
            raise ConstraintViolationError(msg, constraint=constraint) from exc
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
            msg = f"Failed to save mcp installation {installation.catalog_entry_id!r}"
            raise QueryError(msg) from exc
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
        """Fetch a single installation by catalog entry id.

        Returns:
            The matching entity, or ``None`` when no row matches.
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
                    WHERE catalog_entry_id = %s
                    """,
                    (catalog_entry_id,),
                )
                row = await cur.fetchone()
        except Exception as exc:
            reraise_critical(exc)
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

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[McpInstallation, ...]:
        """List recorded installations in a deterministic order.

        Sorted by ``installed_at`` ascending with ``catalog_entry_id``
        as a stable tiebreaker so rows with identical timestamps
        (restores, backfills, clock skew) are always returned in the
        same order across calls.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_MCP_INSTALLATION_LIST_FAILED,
        )
        sql = (
            "SELECT catalog_entry_id, connection_name, installed_at "
            "FROM mcp_installations "
            "ORDER BY installed_at ASC, catalog_entry_id ASC "
            "LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (limit, offset))
                rows = await cur.fetchall()
            # Deserialization runs inside the same try/except so a
            # malformed persisted row surfaces under the same
            # ``PERSISTENCE_MCP_INSTALLATION_LIST_FAILED`` event +
            # ``QueryError`` envelope as a DB failure, not as a raw
            # exception that escapes the persistence boundary.
            return tuple(_row_to_installation(row) for row in rows)
        except Exception as exc:
            reraise_critical(exc)
            msg = "Failed to list mcp installations"
            logger.warning(
                PERSISTENCE_MCP_INSTALLATION_LIST_FAILED,
                limit=limit,
                offset=offset,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                backend="postgres",
            )
            raise QueryError(msg) from exc

    async def delete(self, catalog_entry_id: NotBlankStr) -> bool:
        """Delete an installation.  Returns ``True`` if a row was removed.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM mcp_installations WHERE catalog_entry_id = %s",
                    (catalog_entry_id,),
                )
                deleted = cur.rowcount > 0
        except Exception as exc:
            reraise_critical(exc)
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
