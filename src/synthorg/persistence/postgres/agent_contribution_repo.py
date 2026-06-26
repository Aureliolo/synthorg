"""Postgres append-only repository for agent coordination contributions.

Mirrors the SQLite implementation; the record is stored in a JSONB
``payload`` column and ``recorded_at`` is a TIMESTAMPTZ stamped from an
injected clock at append time.
"""

import json
from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.persistence_errors import QueryError
from synthorg.engine.coordination.attribution import AgentContribution
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.agent_contribution import (
    PERSISTENCE_AGENT_CONTRIBUTION_APPEND_FAILED,
    PERSISTENCE_AGENT_CONTRIBUTION_QUERIED,
    PERSISTENCE_AGENT_CONTRIBUTION_QUERY_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc, validate_pagination_args
from synthorg.persistence.agent_contribution_protocol import (
    AgentContributionFilterSpec,
)

logger = get_logger(__name__)

_INSERT_SQL = """
INSERT INTO agent_contributions (
    agent_id, subtask_id, contribution_score, recorded_at, payload
)
VALUES (%s, %s, %s, %s, %s)
"""
_SELECT_SQL = "SELECT payload FROM agent_contributions"


def _row_to_contribution(payload: object) -> AgentContribution:
    """Deserialise a JSONB ``payload`` column into an ``AgentContribution``.

    Returns:
        The reconstructed ``AgentContribution``.

    Raises:
        QueryError: If the payload is not a JSON object.
    """
    data = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(data, dict):
        msg = f"agent_contributions.payload is not a JSON object: {data!r}"
        raise QueryError(msg)
    return AgentContribution.model_validate(data)


class PostgresAgentContributionRepository:
    """Postgres append-only contribution log.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
        clock: Clock used to stamp ``recorded_at`` at append time.
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._pool = pool
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def append(self, event: AgentContribution, /) -> None:
        """Append one contribution, stamping ``recorded_at`` from the clock.

        Raises:
            QueryError: If the write fails.
        """
        params: tuple[object, ...] = (
            str(event.agent_id),
            str(event.subtask_id),
            event.contribution_score,
            normalize_utc(self._clock.now()),
            Jsonb(event.model_dump(mode="json")),
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, params)
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to append agent contribution"
            logger.warning(
                PERSISTENCE_AGENT_CONTRIBUTION_APPEND_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                agent_id=str(event.agent_id),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: AgentContributionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[AgentContribution, ...]:
        """Return contributions matching the filter, newest-first.

        Returns:
            Matching contributions, newest-first.

        Raises:
            QueryError: If the read fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_AGENT_CONTRIBUTION_QUERY_FAILED
        )
        sql = _SELECT_SQL
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.agent_id is not None:
            clauses.append("agent_id = %s")
            params.append(str(filter_spec.agent_id))
        if filter_spec.subtask_id is not None:
            clauses.append("subtask_id = %s")
            params.append(str(filter_spec.subtask_id))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query agent contributions"
            logger.warning(
                PERSISTENCE_AGENT_CONTRIBUTION_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            records = tuple(_row_to_contribution(r["payload"]) for r in rows)
        except Exception as exc:
            msg = "corrupt agent_contributions row(s)"
            logger.warning(
                PERSISTENCE_AGENT_CONTRIBUTION_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_AGENT_CONTRIBUTION_QUERIED, count=len(records))
        return records

    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete contributions with ``recorded_at < threshold``.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the delete fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM agent_contributions WHERE recorded_at < %s",
                    (normalize_utc(threshold),),
                )
                removed = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge agent contributions"
            logger.warning(
                PERSISTENCE_AGENT_CONTRIBUTION_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return removed
