# module-kind: repository
"""SQLite repository for the durable active-principle store.

Id-keyed CRUD keyed by the principle's ``id`` (canonical string form).
``save`` upserts on the ``id`` primary key. Active principles are the durable
form of meta-loop prompt-tuning changes; the cached read provider drains
``list_items`` at boot to build its in-memory snapshot.
"""

from typing import NoReturn

import aiosqlite

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
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_SELECT_COLS = (
    "id, principle_text, scope, scope_kind, evolution_mode, severity, "
    "created_at, updated_at"
)


def _row_to_principle(row: aiosqlite.Row) -> ActivePrinciple:
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
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        logger.warning(
            STRATEGY_ACTIVE_PRINCIPLE_PERSISTENCE_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to parse active-principle row: {type(exc).__name__}"
        raise QueryError(msg) from exc


class SQLiteActivePrincipleRepository:
    """SQLite-backed durable active-principle store.

    Args:
        db: An open aiosqlite connection.
        write_context: Async write-serialising context manager.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context

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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                principle_text = excluded.principle_text,
                scope = excluded.scope,
                scope_kind = excluded.scope_kind,
                evolution_mode = excluded.evolution_mode,
                severity = excluded.severity,
                updated_at = excluded.updated_at
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
        async with self._write_context():
            try:
                await self._db.execute(sql, params)
                await self._db.commit()
            except (aiosqlite.Error, ValueError) as exc:
                await self._rollback("save")
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
            "WHERE id = ?"
        )
        try:
            async with self._db.execute(sql, (entity_id,)) as cursor:
                row = await cursor.fetchone()
        except (aiosqlite.Error, ValueError) as exc:
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
            "ORDER BY created_at DESC, id ASC LIMIT ? OFFSET ?"
        )
        try:
            async with self._db.execute(sql, (effective_limit, offset)) as cursor:
                rows = await cursor.fetchall()
            return tuple(_row_to_principle(r) for r in rows)
        except QueryError:
            raise
        except (aiosqlite.Error, ValueError) as exc:
            self._raise_query_error("list active principles", exc)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete the active principle for ``str(id)``. ``True`` iff present.

        Returns:
            ``True`` when a row was removed, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM active_principles WHERE id = ?"
        async with self._write_context():
            try:
                async with self._db.execute(sql, (entity_id,)) as cursor:
                    await self._db.commit()
                    return cursor.rowcount > 0
            except (aiosqlite.Error, ValueError) as exc:
                await self._rollback("delete")
                self._raise_query_error("delete active principle", exc)

    async def _rollback(self, operation: str) -> None:
        try:
            await self._db.rollback()
        except aiosqlite.Error as exc:
            logger.warning(
                STRATEGY_ACTIVE_PRINCIPLE_PERSISTENCE_FAILED,
                operation=operation,
                phase="rollback",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    def _raise_query_error(self, operation: str, exc: Exception) -> NoReturn:
        logger.warning(
            STRATEGY_ACTIVE_PRINCIPLE_PERSISTENCE_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to {operation}: {type(exc).__name__}"
        raise QueryError(msg) from exc


__all__ = ["SQLiteActivePrincipleRepository"]
