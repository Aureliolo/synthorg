# module-kind: repository
"""Postgres repository for the durable active-principle store.

Sibling of :class:`SQLiteActivePrincipleRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Id-keyed CRUD keyed by the principle's
``id`` (canonical string form); ``save`` upserts on the primary key.
"""

from typing import NoReturn

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.active_principle import (
    ActivePrinciple,
    PrincipleEvolutionMode,
    ScopeKind,
)
from synthorg.engine.strategy.models import PrincipleSeverity
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.strategy import (
    STRATEGY_ACTIVE_PRINCIPLE_PERSISTENCE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)

logger = get_logger(__name__)

_SELECT_COLS = (
    "id, principle_text, scope, scope_kind, evolution_mode, severity, "
    "created_at, updated_at"
)


def _row_to_principle(row: DictRow) -> ActivePrinciple:
    """Convert a database row into an :class:`ActivePrinciple`.

    Returns:
        The reconstructed principle.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        return ActivePrinciple(
            id=row["id"],
            principle_text=NotBlankStr(str(row["principle_text"])),
            scope=NotBlankStr(str(row["scope"])),
            scope_kind=ScopeKind(str(row["scope_kind"])),
            evolution_mode=PrincipleEvolutionMode(str(row["evolution_mode"])),
            severity=PrincipleSeverity(str(row["severity"])),
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning(
            STRATEGY_ACTIVE_PRINCIPLE_PERSISTENCE_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to parse active-principle row: {type(exc).__name__}"
        raise QueryError(msg) from exc


class PostgresActivePrincipleRepository:
    """Postgres-backed durable active-principle store.

    Args:
        pool: Async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: ActivePrinciple) -> None:
        """Upsert an active principle keyed by ``str(id)``.

        Raises:
            QueryError: On database errors.
        """
        sql = """
            INSERT INTO active_principles (
                id, principle_text, scope, scope_kind, evolution_mode,
                severity, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                principle_text = EXCLUDED.principle_text,
                scope = EXCLUDED.scope,
                scope_kind = EXCLUDED.scope_kind,
                evolution_mode = EXCLUDED.evolution_mode,
                severity = EXCLUDED.severity,
                updated_at = EXCLUDED.updated_at
        """
        params = (
            str(entity.id),
            entity.principle_text,
            entity.scope,
            entity.scope_kind.value,
            entity.evolution_mode.value,
            entity.severity.value,
            format_iso_utc(entity.created_at),
            format_iso_utc(entity.updated_at),
        )
        try:
            async with self._pool.connection() as conn:
                await conn.execute(sql, params)
                await conn.commit()
        except psycopg.Error as exc:
            self._raise_query_error("save active principle", exc)

    async def get(self, entity_id: NotBlankStr) -> ActivePrinciple | None:
        """Get the active principle for ``str(id)``, or ``None``.

        Returns:
            The matching principle, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            f"SELECT {_SELECT_COLS} FROM active_principles "  # noqa: S608
            "WHERE id = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (entity_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            self._raise_query_error("get active principle", exc)
        return None if row is None else _row_to_principle(row)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ActivePrinciple, ...]:
        """List active principles newest-first by ``created_at`` (paginated).

        Returns:
            The matching principles, newest-first.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=STRATEGY_ACTIVE_PRINCIPLE_PERSISTENCE_FAILED
        )
        sql = (
            f"SELECT {_SELECT_COLS} FROM active_principles "  # noqa: S608
            "ORDER BY created_at DESC, id ASC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (effective_limit, offset))
                rows = await cur.fetchall()
            return tuple(_row_to_principle(r) for r in rows)
        except psycopg.Error as exc:
            self._raise_query_error("list active principles", exc)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete the active principle for ``str(id)``. ``True`` iff present.

        Returns:
            ``True`` when a row was removed, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM active_principles WHERE id = %s"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (entity_id,))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            self._raise_query_error("delete active principle", exc)
        return rowcount > 0

    def _raise_query_error(self, operation: str, exc: Exception) -> NoReturn:
        logger.warning(
            STRATEGY_ACTIVE_PRINCIPLE_PERSISTENCE_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to {operation}: {type(exc).__name__}"
        raise QueryError(msg) from exc


__all__ = ["PostgresActivePrincipleRepository"]
