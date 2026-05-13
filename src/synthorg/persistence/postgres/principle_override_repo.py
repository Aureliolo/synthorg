"""Postgres implementation of :class:`PrincipleOverrideRepository`."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import psycopg

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence.principle_override_protocol import (
    _DEFAULT_LIST_LIMIT_100,
    PrincipleOverride,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


class PostgresPrincipleOverrideRepository:
    """Postgres-backed :class:`PrincipleOverrideRepository`."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(
        self,
        scope: NotBlankStr,
        text: NotBlankStr,
        *,
        restored_from: NotBlankStr,
        now: datetime | None = None,
    ) -> None:
        """Insert or update the override at ``scope``."""
        moment = normalize_utc(now or datetime.now(UTC))
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
                    (str(scope), str(text), str(restored_from), moment, moment),
                )
        except psycopg.Error as exc:
            msg = f"Failed to save principle override for scope {scope!r}"
            logger.warning(
                "persistence.principle_override.save_failed",
                scope=str(scope),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, scope: NotBlankStr) -> PrincipleOverride | None:
        """Return the override at ``scope`` if present."""
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
                "persistence.principle_override.get_failed",
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
        """Remove the override; return ``True`` when a row was deleted."""
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
                "persistence.principle_override.delete_failed",
                scope=str(scope),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return removed

    async def list_items(
        self,
        *,
        limit: int = _DEFAULT_LIST_LIMIT_100,
        offset: int = 0,
    ) -> tuple[PrincipleOverride, ...]:
        """List all overrides ordered by ``scope`` ascending."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT scope, text, restored_from, created_at, updated_at
                    FROM principle_overrides
                    ORDER BY scope ASC
                    LIMIT %s OFFSET %s
                    """,
                    (int(limit), int(offset)),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list principle overrides"
            logger.warning(
                "persistence.principle_override.list_failed",
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
