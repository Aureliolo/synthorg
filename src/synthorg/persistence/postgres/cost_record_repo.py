# module-kind: repository
"""Postgres repository implementation for CostRecord."""

from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from synthorg.budget.cost_record import CostRecord
from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.cost_record import (
    PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
    PERSISTENCE_COST_RECORD_QUERIED,
    PERSISTENCE_COST_RECORD_QUERY_FAILED,
    PERSISTENCE_COST_RECORD_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    normalize_utc,
    validate_pagination_args,
)
from synthorg.persistence._shared._cost_record_helpers import (
    resolve_currency_aggregate,
)
from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec

logger = get_logger(__name__)


def _row_to_record(row: dict[str, object]) -> dict[str, object]:
    """Project one row onto the model's field names.

    A row written before ``claim_id`` existed carries ``NULL``, which the
    model rejects outright (the field is non-optional with a generated
    default). Dropping the key lets the default fill it, so a legacy row
    reads back as a record with a fresh key rather than failing the page.

    Returns:
        The row as keyword arguments for :class:`CostRecord`.
    """
    if row.get("claim_id") is None:
        return {key: value for key, value in row.items() if key != "claim_id"}
    return row


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
                # The claim id is the tracker's idempotency key, so a
                # redelivery of the same record is a no-op at the storage
                # layer rather than a second billed row.
                await cur.execute(
                    """
                    INSERT INTO cost_records (
                        agent_id, task_id, project_id, provider, model,
                        input_tokens, output_tokens, cost, currency, timestamp,
                        call_category, prompt_class_id, claim_id, billing_model
                    ) VALUES (
                        %(agent_id)s, %(task_id)s, %(project_id)s, %(provider)s,
                        %(model)s, %(input_tokens)s, %(output_tokens)s,
                        %(cost)s, %(currency)s, %(timestamp)s,
                        %(call_category)s, %(prompt_class_id)s, %(claim_id)s,
                        %(billing_model)s
                    )
                    ON CONFLICT (claim_id, timestamp) DO NOTHING
                    """,
                    {
                        "agent_id": event.agent_id,
                        "task_id": event.task_id,
                        "project_id": event.project_id,
                        "provider": event.provider,
                        "model": event.model,
                        "input_tokens": event.input_tokens,
                        "output_tokens": event.output_tokens,
                        "cost": event.cost,
                        "currency": event.currency,
                        "timestamp": event.timestamp,
                        "call_category": event.call_category,
                        "prompt_class_id": event.prompt_class_id,
                        "claim_id": event.claim_id,
                        "billing_model": event.billing_model.value,
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
        if filter_spec.prompt_class_id is not None:
            clauses.append("prompt_class_id = %s")
            params.append(filter_spec.prompt_class_id)
        if filter_spec.since is not None:
            clauses.append("timestamp >= %s")
            params.append(normalize_utc(filter_spec.since))

        sql = (
            "SELECT agent_id, task_id, project_id, provider, model, "
            "input_tokens, output_tokens, cost, currency, timestamp, "
            "call_category, prompt_class_id, claim_id, billing_model "
            "FROM cost_records"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        # ``agent_id IS NULL`` is ordered explicitly because the two backends
        # disagree on where a NULL sorts by default (SQLite first, Postgres
        # last). With agent_id nullable, leaving it implicit would give the
        # same query a different page order per backend.
        sql += " ORDER BY timestamp DESC, (agent_id IS NULL) ASC,"
        sql += " agent_id ASC, rowid ASC"
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
            records = tuple(
                CostRecord.model_validate(_row_to_record(row)) for row in rows
            )
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
        return resolve_currency_aggregate(row, agent_id=agent_id, task_id=task_id)

    async def purge_before(self, threshold: datetime) -> int:
        """Delete cost records with timestamp before threshold (retention).

        ``threshold`` must be timezone-aware. ``normalize_utc`` tags a naive
        value as UTC, so a caller in another zone would silently delete a
        different window here than on the SQLite twin, from the same
        protocol call.

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If ``threshold`` is naive, or the query fails.
        """
        if threshold.tzinfo is None:
            msg = f"threshold must be timezone-aware, got naive {threshold!r}"
            raise QueryError(msg)
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
