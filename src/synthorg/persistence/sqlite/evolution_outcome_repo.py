# module-kind: repository
"""SQLite repository for the durable evolution-outcome log.

Append-only per :class:`AppendOnlyRepository`: ``append`` inserts one
immutable outcome row (the autoincrement ``rowid`` is the durable
ordering tiebreaker), ``query`` pages newest-first within optional
filters, ``purge_before`` enforces retention, and ``axis_counts``
aggregates per axis for the axes-stats endpoint. Timestamps are stored
as UTC ISO TEXT and ``applied`` as INTEGER 0/1.
"""

import sqlite3
from datetime import datetime
from typing import NoReturn

import aiosqlite

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
from synthorg.persistence._shared import (
    format_iso_utc,
    normalize_utc,
    validate_pagination_args,
)
from synthorg.persistence._shared.evolution_outcome_marshalling import (
    outcome_to_payload,
    row_to_outcome_record,
)
from synthorg.persistence.evolution_outcome_protocol import (
    EvolutionOutcomeFilterSpec,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_SELECT_COLS = "agent_id, axis, applied, proposed_at, recorded_at"


class SQLiteEvolutionOutcomeRepository:
    """SQLite-backed durable evolution-outcome log.

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

    async def append(self, event: EvolutionOutcomeRecord) -> None:
        """Persist an evolution outcome (append-only).

        Raises:
            QueryError: If the database query fails.
        """
        payload = outcome_to_payload(
            event,
            timestamp_serializer=format_iso_utc,
            bool_serializer=int,
        )
        async with self._write_context():
            try:
                await self._db.execute(
                    """
                    INSERT INTO evolution_outcomes (
                        agent_id, axis, applied, proposed_at, recorded_at
                    ) VALUES (
                        :agent_id, :axis, :applied, :proposed_at, :recorded_at
                    )
                    """,
                    payload,
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
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
        sql += " ORDER BY recorded_at DESC, rowid DESC LIMIT ? OFFSET ?"
        params.extend([effective_limit, offset])
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
            records = tuple(row_to_outcome_record(dict(row)) for row in rows)
        except (sqlite3.Error, aiosqlite.Error) as exc:
            self._raise_query_error("query evolution outcomes", exc)
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
        params = [
            format_iso_utc(normalize_utc(since)),
            format_iso_utc(normalize_utc(until)),
        ]
        sql = (
            "SELECT axis, COUNT(*) AS n FROM evolution_outcomes "
            "WHERE recorded_at >= ? AND recorded_at < ? "
            "GROUP BY axis ORDER BY n DESC, axis ASC"
        )
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM evolution_outcomes WHERE recorded_at < ?",
                    (format_iso_utc(normalize_utc(threshold)),),
                ) as cursor:
                    await self._db.commit()
                    return cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                self._raise_query_error("purge evolution outcomes", exc)

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
    """Build the WHERE clause fragments and bound params (SQLite ``?``).

    Returns:
        A ``(clauses, params)`` pair.
    """
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.agent_id is not None:
        clauses.append("agent_id = ?")
        params.append(filter_spec.agent_id)
    if filter_spec.axis is not None:
        clauses.append("axis = ?")
        params.append(filter_spec.axis)
    if filter_spec.applied is not None:
        clauses.append("applied = ?")
        params.append(int(filter_spec.applied))
    if filter_spec.since is not None:
        clauses.append("recorded_at >= ?")
        params.append(format_iso_utc(normalize_utc(filter_spec.since)))
    if filter_spec.until is not None:
        clauses.append("recorded_at < ?")
        params.append(format_iso_utc(normalize_utc(filter_spec.until)))
    return clauses, params


__all__ = ["SQLiteEvolutionOutcomeRepository"]
