# module-kind: repository
"""Postgres implementation of the ``KnowledgeUsageRecordRepository`` protocol.

Postgres sibling of ``persistence/sqlite/knowledge_usage_repo.py``.
``recorded_at`` is stored as TIMESTAMPTZ.
"""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

from datetime import datetime

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.deliverable_receipts import (
    PERSISTENCE_KNOWLEDGE_USAGE_DELETE_FAILED,
    PERSISTENCE_KNOWLEDGE_USAGE_DESERIALIZE_FAILED,
    PERSISTENCE_KNOWLEDGE_USAGE_QUERY_FAILED,
    PERSISTENCE_KNOWLEDGE_USAGE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.knowledge_usage_protocol import (
    KnowledgeUsageFilterSpec,
    KnowledgeUsageRecord,
)

logger = get_logger(__name__)

_COLUMNS = (
    "record_id, task_id, execution_id, project_id, "
    "source_id, chunk_id, content_hash, recorded_at"
)

_INSERT_SQL = f"""\
INSERT INTO knowledge_usage_record ({_COLUMNS}) VALUES (
    %(record_id)s, %(task_id)s, %(execution_id)s, %(project_id)s,
    %(source_id)s, %(chunk_id)s, %(content_hash)s, %(recorded_at)s
)"""


class PostgresKnowledgeUsageRecordRepository:
    """Postgres implementation of ``KnowledgeUsageRecordRepository``.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, record: KnowledgeUsageRecord) -> None:
        """Persist one usage record (append-only).

        Raises:
            DuplicateRecordError: If a record with the same id exists.
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, self._to_row(record))
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            msg = f"Knowledge usage record {record.record_id!r} already exists"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_USAGE_SAVE_FAILED,
                record_id=record.record_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save knowledge usage record {record.record_id!r}"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_USAGE_SAVE_FAILED,
                record_id=record.record_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def append_many(self, records: tuple[KnowledgeUsageRecord, ...]) -> None:
        """Persist many usage records in one transaction (ADR-0001 D7).

        Raises:
            DuplicateRecordError: If any record id already exists.
            QueryError: If the database query fails.
        """
        if not records:
            return
        count = len(records)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.executemany(
                    _INSERT_SQL, [self._to_row(record) for record in records]
                )
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            msg = f"Duplicate id among {count} knowledge usage records"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_USAGE_SAVE_FAILED,
                record_count=count,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save {count} knowledge usage records"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_USAGE_SAVE_FAILED,
                record_count=count,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: KnowledgeUsageFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[KnowledgeUsageRecord, ...]:
        """Return records matching the filter, newest-first.

        Returns:
            The matching records.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_KNOWLEDGE_USAGE_QUERY_FAILED
        )
        where, params = self._build_where(filter_spec)
        sql = (
            f"SELECT {_COLUMNS} FROM knowledge_usage_record WHERE {where} "
            "ORDER BY recorded_at DESC, record_id DESC LIMIT %s OFFSET %s"
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
            msg = "Failed to query knowledge usage records"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_USAGE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_model(r) for r in rows)

    async def purge_before(self, threshold: datetime) -> int:
        """Delete records with ``recorded_at < threshold``.

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
                    "DELETE FROM knowledge_usage_record WHERE recorded_at < %s",
                    (normalize_utc(threshold),),
                )
                count = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge knowledge usage records by threshold"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_USAGE_DELETE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return count

    def _build_where(
        self, filter_spec: KnowledgeUsageFilterSpec
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
        if filter_spec.source_id is not None:
            conditions.append("source_id = %s")
            params.append(filter_spec.source_id)
        where = " AND ".join(conditions) if conditions else "TRUE"
        return where, params

    def _to_row(self, record: KnowledgeUsageRecord) -> dict[str, object]:
        """Flatten a record into a row dict.

        Returns:
            Result of type ``dict[str, object]``.
        """
        data = record.model_dump(mode="json")
        data["recorded_at"] = normalize_utc(record.recorded_at)
        return data

    def _row_to_model(self, row: DictRow) -> KnowledgeUsageRecord:
        """Convert a database row to a ``KnowledgeUsageRecord``.

        Returns:
            Result of type ``KnowledgeUsageRecord``.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        try:
            data = dict(row)
            # psycopg returns TIMESTAMPTZ in the session timezone, not
            # necessarily UTC; normalise on read so the model carries a UTC
            # instant consistently with the write path's ``_to_row``.
            data["recorded_at"] = normalize_utc(data["recorded_at"])
            return KnowledgeUsageRecord.model_validate(data)
        except ValidationError as exc:
            msg = f"Failed to deserialize usage record {row.get('record_id')!r}"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_USAGE_DESERIALIZE_FAILED,
                record_id=row.get("record_id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
