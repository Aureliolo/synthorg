# module-kind: repository
"""Postgres implementation of the ``CodeExecutionRecordRepository`` protocol.

Postgres sibling of ``persistence/sqlite/code_execution_repo.py``.
``executed_at`` is stored as TIMESTAMPTZ and ``passed`` / ``timed_out``
as BOOLEAN.
"""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

from datetime import datetime
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import DictRow, dict_row
from pydantic import ValidationError

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.deliverable_receipts import (
    PERSISTENCE_CODE_EXECUTION_DELETE_FAILED,
    PERSISTENCE_CODE_EXECUTION_DESERIALIZE_FAILED,
    PERSISTENCE_CODE_EXECUTION_QUERY_FAILED,
    PERSISTENCE_CODE_EXECUTION_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionFilterSpec,
    CodeExecutionRecord,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_COLUMNS = (
    "record_id, task_id, execution_id, project_id, purpose, command, "
    "returncode, passed, timed_out, stdout_tail, stderr_tail, executed_at"
)

_INSERT_SQL = f"""\
INSERT INTO code_execution_record ({_COLUMNS}) VALUES (
    %(record_id)s, %(task_id)s, %(execution_id)s, %(project_id)s, %(purpose)s,
    %(command)s, %(returncode)s, %(passed)s, %(timed_out)s, %(stdout_tail)s,
    %(stderr_tail)s, %(executed_at)s
)"""


class PostgresCodeExecutionRecordRepository:
    """Postgres implementation of ``CodeExecutionRecordRepository``.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, record: CodeExecutionRecord) -> None:
        """Persist one execution record (append-only).

        Raises:
            DuplicateRecordError: If a record with the same id exists.
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, self._to_row(record))
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            msg = f"Code execution record {record.record_id!r} already exists"
            logger.warning(
                PERSISTENCE_CODE_EXECUTION_SAVE_FAILED,
                record_id=record.record_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save code execution record {record.record_id!r}"
            logger.warning(
                PERSISTENCE_CODE_EXECUTION_SAVE_FAILED,
                record_id=record.record_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: CodeExecutionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CodeExecutionRecord, ...]:
        """Return records matching the filter, newest-first.

        Returns:
            The matching records.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CODE_EXECUTION_QUERY_FAILED
        )
        where, params = self._build_where(filter_spec)
        sql = (
            f"SELECT {_COLUMNS} FROM code_execution_record WHERE {where} "
            "ORDER BY executed_at DESC, record_id DESC LIMIT %s OFFSET %s"
        )
        all_params = [*params, limit, offset]
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, all_params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query code execution records"
            logger.warning(
                PERSISTENCE_CODE_EXECUTION_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_model(r) for r in rows)

    async def purge_before(self, threshold: datetime) -> int:
        """Delete records with ``executed_at < threshold``.

        Args:
            threshold: Timezone-aware UTC timestamp. A naive datetime is
                rejected to prevent silent local-time misinterpretation
                deleting the wrong retention window.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If *threshold* is naive or the database query fails.
        """
        if threshold.tzinfo is None:
            msg = "threshold must be timezone-aware; a naive datetime is rejected"
            raise QueryError(msg)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM code_execution_record WHERE executed_at < %s",
                    (normalize_utc(threshold),),
                )
                count = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge code execution records by threshold"
            logger.warning(
                PERSISTENCE_CODE_EXECUTION_DELETE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return count

    def _build_where(
        self, filter_spec: CodeExecutionFilterSpec
    ) -> tuple[str, list[object]]:
        """Build the WHERE clause + positional params for ``filter_spec``.

        Returns:
            ``(where_clause, params)`` without the leading ``WHERE``.
        """
        conditions: list[str] = []
        params: list[object] = []
        if filter_spec.execution_id is not None:
            conditions.append("execution_id = %s")
            params.append(filter_spec.execution_id)
        if filter_spec.task_id is not None:
            conditions.append("task_id = %s")
            params.append(filter_spec.task_id)
        if filter_spec.project_id is not None:
            conditions.append("project_id = %s")
            params.append(filter_spec.project_id)
        if filter_spec.purpose is not None:
            conditions.append("purpose = %s")
            params.append(filter_spec.purpose.value)
        where = " AND ".join(conditions) if conditions else "TRUE"
        return where, params

    def _to_row(self, record: CodeExecutionRecord) -> dict[str, object]:
        """Flatten a record into a row dict.

        Returns:
            Result of type ``dict[str, object]``.
        """
        data = record.model_dump(mode="json")
        data["passed"] = record.passed
        data["timed_out"] = record.timed_out
        data["executed_at"] = normalize_utc(record.executed_at)
        return data

    def _row_to_model(self, row: DictRow) -> CodeExecutionRecord:
        """Convert a database row to a ``CodeExecutionRecord``.

        Returns:
            Result of type ``CodeExecutionRecord``.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        try:
            data = dict(row)
            # psycopg returns TIMESTAMPTZ in the session timezone, not
            # necessarily UTC; normalise on read so the model carries a UTC
            # instant consistently with the write path's ``_to_row``.
            data["executed_at"] = normalize_utc(data["executed_at"])
            return CodeExecutionRecord.model_validate(data)
        except ValidationError as exc:
            msg = f"Failed to deserialize code record {row.get('record_id')!r}"
            logger.warning(
                PERSISTENCE_CODE_EXECUTION_DESERIALIZE_FAILED,
                record_id=row.get("record_id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
