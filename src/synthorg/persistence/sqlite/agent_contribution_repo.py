"""SQLite append-only repository for agent coordination contributions.

``AgentContribution`` carries no timestamp, so the repository stamps a
``recorded_at`` from an injected clock at append time; the record
round-trips through a JSON ``payload`` column. Newest-first ordering
uses the autoincrement surrogate ``id``.
"""

import json
import sqlite3
from datetime import datetime

import aiosqlite

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
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence._shared.datetime_marshaller import format_iso_utc
from synthorg.persistence.agent_contribution_protocol import (
    AgentContributionFilterSpec,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_INSERT_SQL = """
INSERT INTO agent_contributions (
    agent_id, subtask_id, contribution_score, recorded_at, payload
)
VALUES (?, ?, ?, ?, ?)
"""
_SELECT_SQL = "SELECT payload FROM agent_contributions"


def _row_to_contribution(payload: object) -> AgentContribution:
    """Deserialise a JSON ``payload`` column into an ``AgentContribution``.

    Returns:
        The reconstructed ``AgentContribution``.

    Raises:
        QueryError: If the payload is not a JSON object.
    """
    data = json.loads(str(payload)) if payload else {}
    if not isinstance(data, dict):
        msg = f"agent_contributions.payload is not a JSON object: {data!r}"
        raise QueryError(msg)
    return AgentContribution.model_validate(data)


class SQLiteAgentContributionRepository:
    """SQLite append-only contribution log.

    Args:
        db: An open ``aiosqlite.Connection``.
        write_context: Shared backend write context.
        clock: Clock used to stamp ``recorded_at`` at append time.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
        clock: Clock | None = None,
    ) -> None:
        self._db = db
        self._write_context = write_context
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def append(self, event: AgentContribution, /) -> None:
        """Append one contribution, stamping ``recorded_at`` from the clock.

        Raises:
            QueryError: If the write fails.
        """
        params = (
            str(event.agent_id),
            str(event.subtask_id),
            event.contribution_score,
            format_iso_utc(self._clock.now()),
            json.dumps(event.model_dump(mode="json"), sort_keys=True),
        )
        async with self._write_context():
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                await self._db.execute(_INSERT_SQL, params)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
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
            clauses.append("agent_id = ?")
            params.append(str(filter_spec.agent_id))
        if filter_spec.subtask_id is not None:
            clauses.append("subtask_id = ?")
            params.append(str(filter_spec.subtask_id))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = list(await cursor.fetchall())
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query agent contributions"
            logger.warning(
                PERSISTENCE_AGENT_CONTRIBUTION_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            records = tuple(_row_to_contribution(dict(r)["payload"]) for r in rows)
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
            QueryError: If *threshold* is naive or the delete fails.
        """
        if threshold.tzinfo is None:
            msg = "agent contribution purge threshold must be timezone-aware"
            raise QueryError(msg)
        async with self._write_context():
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                async with self._db.execute(
                    "DELETE FROM agent_contributions WHERE recorded_at < ?",
                    (format_iso_utc(threshold),),
                ) as cursor:
                    removed = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = "Failed to purge agent contributions"
                logger.warning(
                    PERSISTENCE_AGENT_CONTRIBUTION_QUERY_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return removed

    async def _safe_rollback(self) -> None:
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                PERSISTENCE_AGENT_CONTRIBUTION_APPEND_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                rollback_failed=True,
            )
