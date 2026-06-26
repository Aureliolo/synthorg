"""Postgres append-only repository for promotion/demotion history.

Mirrors the SQLite implementation; the nested ``PromotionRecord`` is
stored in a JSONB ``payload`` column (vs JSON text on SQLite).
"""

import json
from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.hr.promotion.models import PromotionRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.promotion_history import (
    PERSISTENCE_PROMOTION_HISTORY_APPEND_FAILED,
    PERSISTENCE_PROMOTION_HISTORY_QUERIED,
    PERSISTENCE_PROMOTION_HISTORY_QUERY_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc, validate_pagination_args
from synthorg.persistence.promotion_history_protocol import (
    PromotionHistoryFilterSpec,
)

logger = get_logger(__name__)

_INSERT_SQL = """
INSERT INTO promotion_history (id, agent_id, direction, effective_at, payload)
VALUES (%s, %s, %s, %s, %s)
"""
_SELECT_SQL = "SELECT payload FROM promotion_history"


def _record_to_params(record: PromotionRecord) -> tuple[object, ...]:
    """Marshal a ``PromotionRecord`` into positional INSERT params.

    Returns:
        Positional params: ``(id, agent_id, direction, effective_at, payload)``.
    """
    return (
        str(record.id),
        str(record.agent_id),
        record.direction.value,
        normalize_utc(record.effective_at),
        Jsonb(record.model_dump(mode="json")),
    )


def _row_to_record(payload: object) -> PromotionRecord:
    """Deserialise a JSONB ``payload`` column into a ``PromotionRecord``.

    Returns:
        The reconstructed ``PromotionRecord``.

    Raises:
        QueryError: If the payload is not a JSON object.
    """
    data = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(data, dict):
        msg = f"promotion_history.payload is not a JSON object: {data!r}"
        raise QueryError(msg)
    return PromotionRecord.model_validate(data)


class PostgresPromotionHistoryRepository:
    """Postgres append-only promotion/demotion history store.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, event: PromotionRecord, /) -> None:
        """Append one immutable promotion record.

        Raises:
            QueryError: If the write fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, _record_to_params(event))
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to append promotion record"
            logger.warning(
                PERSISTENCE_PROMOTION_HISTORY_APPEND_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                agent_id=str(event.agent_id),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: PromotionHistoryFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[PromotionRecord, ...]:
        """Return promotion records matching the filter, newest-first.

        Returns:
            Matching records, newest-first by ``effective_at``.

        Raises:
            QueryError: If the read fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PROMOTION_HISTORY_QUERY_FAILED
        )
        sql = _SELECT_SQL
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.agent_id is not None:
            clauses.append("agent_id = %s")
            params.append(str(filter_spec.agent_id))
        if filter_spec.direction is not None:
            clauses.append("direction = %s")
            params.append(filter_spec.direction.value)
        if filter_spec.since is not None:
            clauses.append("effective_at >= %s")
            params.append(normalize_utc(filter_spec.since))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY effective_at DESC, id DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query promotion history"
            logger.warning(
                PERSISTENCE_PROMOTION_HISTORY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            records = tuple(_row_to_record(r["payload"]) for r in rows)
        except Exception as exc:
            msg = "corrupt promotion_history row(s)"
            logger.warning(
                PERSISTENCE_PROMOTION_HISTORY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_PROMOTION_HISTORY_QUERIED,
            count=len(records),
        )
        return records

    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete records with ``effective_at < threshold``.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the delete fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM promotion_history WHERE effective_at < %s",
                    (normalize_utc(threshold),),
                )
                removed = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge promotion history"
            logger.warning(
                PERSISTENCE_PROMOTION_HISTORY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return removed
