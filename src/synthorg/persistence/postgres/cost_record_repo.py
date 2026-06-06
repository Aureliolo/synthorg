# module-kind: repository
"""Postgres repository implementation for CostRecord."""

from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.core.normalization import parse_comma_list
from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.cost_record import (
    PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
    PERSISTENCE_COST_RECORD_AGGREGATED,
    PERSISTENCE_COST_RECORD_QUERIED,
    PERSISTENCE_COST_RECORD_QUERY_FAILED,
    PERSISTENCE_COST_RECORD_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    normalize_utc,
    safe_float,
    safe_int,
    validate_pagination_args,
)

if TYPE_CHECKING:
    from datetime import datetime

    from psycopg_pool import AsyncConnectionPool

    from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec

logger = get_logger(__name__)


class PostgresCostRecordRepository:
    """Postgres implementation of the CostRecordRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, event: CostRecord) -> None:
        """Persist a cost record (append-only per AppendOnlyRepository).

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO cost_records (
                        agent_id, task_id, provider, model, input_tokens,
                        output_tokens, cost, currency, timestamp,
                        call_category
                    ) VALUES (
                        %(agent_id)s, %(task_id)s, %(provider)s, %(model)s,
                        %(input_tokens)s, %(output_tokens)s, %(cost)s,
                        %(currency)s, %(timestamp)s, %(call_category)s
                    )
                    """,
                    {
                        "agent_id": event.agent_id,
                        "task_id": event.task_id,
                        "provider": event.provider,
                        "model": event.model,
                        "input_tokens": event.input_tokens,
                        "output_tokens": event.output_tokens,
                        "cost": event.cost,
                        "currency": event.currency,
                        "timestamp": event.timestamp,
                        "call_category": event.call_category,
                    },
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save cost record for agent {event.agent_id!r}"
            logger.warning(
                PERSISTENCE_COST_RECORD_SAVE_FAILED,
                agent_id=event.agent_id,
                task_id=event.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: CostRecordFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CostRecord, ...]:
        """Query cost records matching filter spec with pagination.

        Raises:
            QueryError: If the query fails or pagination is out of range.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_COST_RECORD_QUERY_FAILED
        )
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.agent_id is not None:
            clauses.append("agent_id = %s")
            params.append(filter_spec.agent_id)
        if filter_spec.task_id is not None:
            clauses.append("task_id = %s")
            params.append(filter_spec.task_id)

        sql = (
            "SELECT agent_id, task_id, provider, model, input_tokens, "
            "output_tokens, cost, currency, timestamp, call_category "
            "FROM cost_records"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC, agent_id ASC, rowid ASC"
        sql += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query cost records"
            logger.warning(
                PERSISTENCE_COST_RECORD_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            records = tuple(CostRecord.model_validate(row) for row in rows)
        except ValidationError as exc:
            msg = "Failed to deserialize cost records"
            logger.warning(
                PERSISTENCE_COST_RECORD_QUERY_FAILED,
                error_type="ValidationError",
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_COST_RECORD_QUERIED, count=len(records))
        return records

    async def aggregate(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> float:
        """Sum total cost, optionally filtered by agent and/or task.

        Raises :class:`MixedCurrencyAggregationError` when the matched rows
        span multiple currencies.  The distinct-currency probe and the
        ``SUM`` run in a **single** aggregating query
        (``COUNT(DISTINCT)`` + ``STRING_AGG(DISTINCT)`` + ``SUM``) so the
        two observations share one snapshot and a concurrent commit
        cannot change the result between them.

        Returns:
            Result of type ``float``.

        Raises:
            QueryError: If the database query fails.
            MixedCurrencyAggregationError: If aggregated rows mix currencies.
        """
        conditions: list[str] = []
        params: list[str] = []
        if agent_id is not None:
            conditions.append("agent_id = %s")
            params.append(agent_id)
        if task_id is not None:
            conditions.append("task_id = %s")
            params.append(task_id)
        where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        # where_clause is built from fixed column names only; user values
        # go through bound %s parameters.
        agg_select = (
            "SELECT "
            "COUNT(DISTINCT currency) AS distinct_count, "
            "STRING_AGG(DISTINCT currency, ',') AS currencies, "
            "COALESCE(SUM(cost), 0.0) AS total_cost "
            "FROM cost_records"
        )
        agg_sql = f"{agg_select}{where_clause}"

        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(agg_sql, params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to aggregate cost records"
            logger.warning(
                PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            msg = "aggregate query returned no rows"
            logger.error(
                PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
                agent_id=agent_id,
                error=msg,
            )
            raise QueryError(msg)
        distinct_count = safe_int(row[0], default=0)
        currencies_csv = row[1]
        total = safe_float(row[2], default=0.0)
        if distinct_count > 1:
            distinct = frozenset(parse_comma_list(currencies_csv))
            logger.error(
                PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                currencies=sorted(distinct),
                error="mixed-currency aggregation rejected",
            )
            mixed_msg = "Cannot aggregate costs across mixed currencies"
            raise MixedCurrencyAggregationError(
                mixed_msg,
                currencies=distinct,
                agent_id=agent_id,
                task_id=task_id,
            )
        logger.debug(
            PERSISTENCE_COST_RECORD_AGGREGATED,
            agent_id=agent_id,
            total_cost=total,
        )
        return total

    async def purge_before(self, threshold: datetime) -> int:
        """Delete cost records with timestamp before threshold (retention).

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM cost_records WHERE timestamp < %s",
                    (normalize_utc(threshold),),
                )
                deleted_count = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge cost records by threshold"
            logger.warning(
                PERSISTENCE_COST_RECORD_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted_count
