# module-kind: repository
"""Postgres repository for the durable evolution-outcome log.

Append-only per :class:`AppendOnlyRepository`. The ``BIGSERIAL`` id is
the durable ordering tiebreaker; ``applied`` is a native BOOLEAN and the
timestamps are TIMESTAMPTZ.
"""

from datetime import datetime
from typing import NoReturn

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.meta.evolution.outcome_models import EvolutionOutcomeRecord
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.evolution_outcome import (
    PERSISTENCE_EVOLUTION_OUTCOME_QUERIED,
    PERSISTENCE_EVOLUTION_OUTCOME_QUERY_FAILED,
    PERSISTENCE_EVOLUTION_OUTCOME_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc, validate_pagination_args
from synthorg.persistence._shared.evolution_outcome_marshalling import (
    outcome_to_payload,
    row_to_outcome_record,
)
from synthorg.persistence.evolution_outcome_protocol import (
    EvolutionOutcomeFilterSpec,
)

logger = get_logger(__name__)

_SELECT_COLS = "agent_id, axis, applied, proposed_at, recorded_at"


def _identity(value: datetime) -> object:
    """Pass a native datetime straight through to the TIMESTAMPTZ column.

    Returns:
        The value unchanged.
    """
    return value


class PostgresEvolutionOutcomeRepository:
    """Postgres-backed durable evolution-outcome log.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, event: EvolutionOutcomeRecord) -> None:
        """Persist an evolution outcome (append-only).

        Raises:
            QueryError: If the database query fails.
        """
        payload = outcome_to_payload(
            event,
            timestamp_serializer=_identity,
            bool_serializer=bool,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO evolution_outcomes (
                        agent_id, axis, applied, proposed_at, recorded_at
                    ) VALUES (
                        %(agent_id)s, %(axis)s, %(applied)s,
                        %(proposed_at)s, %(recorded_at)s
                    )
                    """,
                    payload,
                )
                await conn.commit()
        except psycopg.Error as exc:
            self._raise_query_error("save evolution outcome", exc)

    async def query(
        self,
        filter_spec: EvolutionOutcomeFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[EvolutionOutcomeRecord, ...]:
        """Query outcomes matching filter spec, newest-first, paginated.

        Returns:
            The matching records.

        Raises:
            QueryError: If the query fails or pagination is out of range.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_EVOLUTION_OUTCOME_QUERY_FAILED
        )
        clauses, params = _build_where(filter_spec)
        sql = f"SELECT {_SELECT_COLS} FROM evolution_outcomes"  # noqa: S608
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY recorded_at DESC, id DESC LIMIT %s OFFSET %s"
        params.extend([effective_limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            self._raise_query_error("query evolution outcomes", exc)
        records = tuple(row_to_outcome_record(row) for row in rows)
        logger.debug(PERSISTENCE_EVOLUTION_OUTCOME_QUERIED, count=len(records))
        return records

    async def axis_counts(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[tuple[NotBlankStr, int], ...]:
        """Count outcomes per axis within ``[since, until)``.

        Returns:
            ``(axis, count)`` pairs, highest count first.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            "SELECT axis, COUNT(*) AS n FROM evolution_outcomes "
            "WHERE recorded_at >= %s AND recorded_at < %s "
            "GROUP BY axis ORDER BY n DESC, axis ASC"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, [normalize_utc(since), normalize_utc(until)])
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            self._raise_query_error("aggregate evolution outcomes", exc)
        return tuple((NotBlankStr(str(r["axis"])), int(r["n"])) for r in rows)

    async def purge_before(self, threshold: datetime) -> int:
        """Delete outcomes recorded before threshold (retention).

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the threshold is naive or the query fails.
        """
        if threshold.tzinfo is None:
            msg = f"threshold must be timezone-aware, got naive {threshold!r}"
            raise QueryError(msg)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM evolution_outcomes WHERE recorded_at < %s",
                    (normalize_utc(threshold),),
                )
                deleted = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            self._raise_query_error("purge evolution outcomes", exc)
        return deleted

    def _raise_query_error(self, operation: str, exc: Exception) -> NoReturn:
        event = (
            PERSISTENCE_EVOLUTION_OUTCOME_SAVE_FAILED
            if operation.startswith("save")
            else PERSISTENCE_EVOLUTION_OUTCOME_QUERY_FAILED
        )
        logger.warning(
            event,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to {operation}: {type(exc).__name__}"
        raise QueryError(msg) from exc


def _build_where(
    filter_spec: EvolutionOutcomeFilterSpec,
) -> tuple[list[str], list[object]]:
    """Build the WHERE clause fragments and bound params (Postgres ``%s``).

    Returns:
        A ``(clauses, params)`` pair.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.agent_id is not None:
        clauses.append("agent_id = %s")
        params.append(filter_spec.agent_id)
    if filter_spec.axis is not None:
        clauses.append("axis = %s")
        params.append(filter_spec.axis)
    if filter_spec.applied is not None:
        clauses.append("applied = %s")
        params.append(filter_spec.applied)
    if filter_spec.since is not None:
        clauses.append("recorded_at >= %s")
        params.append(normalize_utc(filter_spec.since))
    if filter_spec.until is not None:
        clauses.append("recorded_at < %s")
        params.append(normalize_utc(filter_spec.until))
    return clauses, params


__all__ = ["PostgresEvolutionOutcomeRepository"]
