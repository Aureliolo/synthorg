# module-kind: repository
"""SQLite repository for the durable department store.

Id-keyed CRUD keyed by ``str(id)`` plus a bespoke ``get_by_name`` read.
``save`` upserts on the ``id`` primary key; the ``name`` column carries a
UNIQUE constraint so a colliding name surfaces as a ``QueryError``.
"""

from typing import NoReturn

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.company import DEPARTMENT_PERSISTENCE_FAILED
from synthorg.organization.department_record import DepartmentRecord
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_SELECT_COLS = "id, name, description, created_at, updated_at"


def _row_to_record(row: aiosqlite.Row) -> DepartmentRecord:
    """Convert a database row into a :class:`DepartmentRecord`.

    Returns:
        The reconstructed department record.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        return DepartmentRecord(
            id=row["id"],
            name=NotBlankStr(str(row["name"])),
            description=str(row["description"]),
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
        )
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        logger.warning(
            DEPARTMENT_PERSISTENCE_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to parse department row: {type(exc).__name__}"
        raise QueryError(msg) from exc


class SQLiteDepartmentRepository:
    """SQLite-backed durable department store.

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

    async def save(self, entity: DepartmentRecord) -> None:
        """Upsert a department keyed by ``str(id)``.

        Raises:
            QueryError: On database errors (including a name collision).
        """
        sql = """
            INSERT INTO departments (id, name, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                updated_at = excluded.updated_at
        """
        params = (
            str(entity.id),
            entity.name,
            entity.description,
            format_iso_utc(entity.created_at),
            format_iso_utc(entity.updated_at),
        )
        async with self._write_context():
            try:
                await self._db.execute(sql, params)
                await self._db.commit()
            except (aiosqlite.Error, ValueError) as exc:
                await self._rollback("save")
                self._raise_query_error("save department", exc)

    async def get(self, entity_id: NotBlankStr) -> DepartmentRecord | None:
        """Get the department for ``str(id)``, or ``None``.

        Returns:
            The matching department record, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {_SELECT_COLS} FROM departments WHERE id = ?"  # noqa: S608
        try:
            async with self._db.execute(sql, (entity_id,)) as cursor:
                row = await cursor.fetchone()
        except (aiosqlite.Error, ValueError) as exc:
            self._raise_query_error("get department", exc)
        return None if row is None else _row_to_record(row)

    async def get_by_name(self, name: NotBlankStr) -> DepartmentRecord | None:
        """Get the department with ``name``, or ``None``.

        Returns:
            The matching department record, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {_SELECT_COLS} FROM departments WHERE name = ?"  # noqa: S608
        try:
            async with self._db.execute(sql, (name,)) as cursor:
                row = await cursor.fetchone()
        except (aiosqlite.Error, ValueError) as exc:
            self._raise_query_error("get department by name", exc)
        return None if row is None else _row_to_record(row)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DepartmentRecord, ...]:
        """List departments newest-first by ``created_at`` (paginated).

        Returns:
            The departments, newest-first.

        Raises:
            QueryError: If the database query fails or pagination args
                are invalid.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=DEPARTMENT_PERSISTENCE_FAILED
        )
        sql = (
            f"SELECT {_SELECT_COLS} FROM departments "  # noqa: S608
            "ORDER BY created_at DESC, id ASC LIMIT ? OFFSET ?"
        )
        try:
            async with self._db.execute(sql, (effective_limit, offset)) as cursor:
                rows = await cursor.fetchall()
            return tuple(_row_to_record(r) for r in rows)
        except QueryError:
            raise
        except (aiosqlite.Error, ValueError) as exc:
            self._raise_query_error("list departments", exc)

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete the department for ``str(id)``. ``True`` iff present.

        Returns:
            ``True`` when a row was removed, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM departments WHERE id = ?"
        async with self._write_context():
            try:
                async with self._db.execute(sql, (entity_id,)) as cursor:
                    await self._db.commit()
                    return cursor.rowcount > 0
            except (aiosqlite.Error, ValueError) as exc:
                await self._rollback("delete")
                self._raise_query_error("delete department", exc)

    async def _rollback(self, operation: str) -> None:
        try:
            await self._db.rollback()
        except aiosqlite.Error as exc:
            logger.warning(
                DEPARTMENT_PERSISTENCE_FAILED,
                operation=operation,
                phase="rollback",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    def _raise_query_error(self, operation: str, exc: Exception) -> NoReturn:
        logger.warning(
            DEPARTMENT_PERSISTENCE_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to {operation}: {type(exc).__name__}"
        raise QueryError(msg) from exc


__all__ = ["SQLiteDepartmentRepository"]
