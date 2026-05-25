"""Postgres implementation of :class:`PrincipleOverrideRepository`."""

from typing import TYPE_CHECKING

import psycopg

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_PRINCIPLE_OVERRIDE_DELETE_FAILED,
    PERSISTENCE_PRINCIPLE_OVERRIDE_GET_FAILED,
    PERSISTENCE_PRINCIPLE_OVERRIDE_LIST_FAILED,
    PERSISTENCE_PRINCIPLE_OVERRIDE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc, validate_pagination_args
from synthorg.persistence.principle_override_protocol import PrincipleOverride

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


class PostgresPrincipleOverrideRepository:
    """Postgres-backed :class:`PrincipleOverrideRepository`."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: PrincipleOverride) -> None:
        """Insert or update the override at ``scope``.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO principle_overrides
                        (scope, text, restored_from, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (scope) DO UPDATE SET
                        text = EXCLUDED.text,
                        restored_from = EXCLUDED.restored_from,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        str(entity.scope),
                        str(entity.text),
                        str(entity.restored_from),
                        normalize_utc(entity.created_at),
                        normalize_utc(entity.updated_at),
                    ),
                )
        except psycopg.Error as exc:
            msg = f"Failed to save principle override for scope {entity.scope!r}"
            logger.warning(
                PERSISTENCE_PRINCIPLE_OVERRIDE_SAVE_FAILED,
                scope=str(entity.scope),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, scope: NotBlankStr) -> PrincipleOverride | None:
        """Return the override at ``scope`` if present.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT scope, text, restored_from, created_at, updated_at
                    FROM principle_overrides WHERE scope = %s
                    """,
                    (str(scope),),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to load principle override for scope {scope!r}"
            logger.warning(
                PERSISTENCE_PRINCIPLE_OVERRIDE_GET_FAILED,
                scope=str(scope),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return PrincipleOverride(
            scope=NotBlankStr(row[0]),
            text=NotBlankStr(row[1]),
            restored_from=NotBlankStr(row[2]),
            created_at=normalize_utc(row[3]),
            updated_at=normalize_utc(row[4]),
        )

    async def delete(self, scope: NotBlankStr) -> bool:
        """Remove the override; return ``True`` when a row was deleted.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM principle_overrides WHERE scope = %s",
                    (str(scope),),
                )
                removed = cur.rowcount > 0
        except psycopg.Error as exc:
            msg = f"Failed to delete principle override for scope {scope!r}"
            logger.warning(
                PERSISTENCE_PRINCIPLE_OVERRIDE_DELETE_FAILED,
                scope=str(scope),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return removed

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[PrincipleOverride, ...]:
        """List all overrides ordered by ``scope`` ascending.

        Raises:
            QueryError: If the query fails or pagination is out of range.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PRINCIPLE_OVERRIDE_LIST_FAILED
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT scope, text, restored_from, created_at, updated_at
                    FROM principle_overrides
                    ORDER BY scope ASC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list principle overrides"
            logger.warning(
                PERSISTENCE_PRINCIPLE_OVERRIDE_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(
            PrincipleOverride(
                scope=NotBlankStr(r[0]),
                text=NotBlankStr(r[1]),
                restored_from=NotBlankStr(r[2]),
                created_at=normalize_utc(r[3]),
                updated_at=normalize_utc(r[4]),
            )
            for r in rows
        )
