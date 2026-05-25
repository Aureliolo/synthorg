"""SQLite repository implementation for :class:`ResearchRun`.

Persists each research run as a single JSON blob (the run is the durable,
replayable record) alongside denormalised ``run_id`` / ``brief_id`` /
``project_id`` / ``status`` / ``created_at`` columns used for filtering and
ordering.
"""

import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_RESEARCH_RUN_COUNT_FAILED,
    PERSISTENCE_RESEARCH_RUN_COUNTED,
    PERSISTENCE_RESEARCH_RUN_DELETE_FAILED,
    PERSISTENCE_RESEARCH_RUN_DESERIALIZE_FAILED,
    PERSISTENCE_RESEARCH_RUN_FETCH_FAILED,
    PERSISTENCE_RESEARCH_RUN_FETCHED,
    PERSISTENCE_RESEARCH_RUN_LIST_FAILED,
    PERSISTENCE_RESEARCH_RUN_LISTED,
    PERSISTENCE_RESEARCH_RUN_QUERIED,
    PERSISTENCE_RESEARCH_RUN_QUERY_FAILED,
    PERSISTENCE_RESEARCH_RUN_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import format_iso_utc, validate_pagination_args
from synthorg.research.models import ResearchRun

if TYPE_CHECKING:
    from collections.abc import Iterable

    from synthorg.persistence.research_protocol import (
        ResearchRunFilter,
        ResearchRunKey,
    )
    from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000

_UPSERT_SQL = """\
INSERT INTO research_runs (run_id, brief_id, project_id, status, created_at, run_json)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(run_id) DO UPDATE SET
    brief_id=excluded.brief_id,
    project_id=excluded.project_id,
    status=excluded.status,
    created_at=excluded.created_at,
    run_json=excluded.run_json"""


def _row_to_run(row: aiosqlite.Row) -> ResearchRun:
    """Reconstruct a :class:`ResearchRun` from its persisted JSON blob.

    Returns:
        Result of type ``ResearchRun``.
    """
    return ResearchRun.model_validate_json(str(row["run_json"]))


class SQLiteResearchRunRepository:
    """SQLite-backed research-run repository."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context

    @staticmethod
    def _row_params(entity: ResearchRun) -> tuple[object, ...]:
        """Row params.

        Returns:
            The matching collection.
        """
        return (
            entity.run_id,
            entity.brief_id,
            entity.project_id,
            entity.status.value,
            format_iso_utc(entity.created_at),
            entity.model_dump_json(),
        )

    async def _safe_rollback(self, *, event: str) -> None:
        """Safe rollback."""
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                event,
                error_type=type(rollback_exc).__name__,
                error=safe_error_description(rollback_exc),
                rollback_failed=True,
            )

    async def save(self, entity: ResearchRun) -> None:
        """Persist a run row via upsert (PK ``run_id``).

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(_UPSERT_SQL, self._row_params(entity))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(event=PERSISTENCE_RESEARCH_RUN_SAVE_FAILED)
                msg = f"Failed to save research run {entity.run_id!r}"
                logger.warning(
                    PERSISTENCE_RESEARCH_RUN_SAVE_FAILED,
                    run_id=entity.run_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: ResearchRunKey) -> ResearchRun | None:
        """Retrieve a run by ``run_id``.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            cursor = await self._db.execute(
                "SELECT run_json FROM research_runs WHERE run_id = ?",
                (entity_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch research run {entity_id!r}"
            logger.warning(
                PERSISTENCE_RESEARCH_RUN_FETCH_FAILED,
                run_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(
                PERSISTENCE_RESEARCH_RUN_FETCHED, run_id=entity_id, found=False
            )
            return None
        run = self._rows_to_tuple((row,))[0]
        logger.debug(PERSISTENCE_RESEARCH_RUN_FETCHED, run_id=entity_id, found=True)
        return run

    async def delete(self, entity_id: ResearchRunKey) -> bool:
        """Delete a run by ``run_id``.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM research_runs WHERE run_id = ?",
                    (entity_id,),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(event=PERSISTENCE_RESEARCH_RUN_DELETE_FAILED)
                msg = f"Failed to delete research run {entity_id!r}"
                logger.warning(
                    PERSISTENCE_RESEARCH_RUN_DELETE_FAILED,
                    run_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return cursor.rowcount > 0

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ResearchRun, ...]:
        """List all runs, most-recent first.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_RESEARCH_RUN_LIST_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        try:
            cursor = await self._db.execute(
                """SELECT run_json FROM research_runs
                   ORDER BY created_at DESC, run_id DESC
                   LIMIT ? OFFSET ?""",
                (effective_limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list research runs"
            logger.warning(
                PERSISTENCE_RESEARCH_RUN_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return self._rows_to_tuple(tuple(rows))

    async def query(
        self,
        filter_spec: ResearchRunFilter,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ResearchRun, ...]:
        """Return runs matching the filter, most-recent first.

        Returns:
            Tuple of (items, next_cursor) for paginated iteration.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_RESEARCH_RUN_QUERY_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        where_sql, params = _build_query_sql(filter_spec)
        sql = (
            f"SELECT run_json {where_sql} "
            "ORDER BY created_at DESC, run_id DESC LIMIT ? OFFSET ?"
        )
        params = (*params, effective_limit, offset)
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query research runs"
            logger.warning(
                PERSISTENCE_RESEARCH_RUN_QUERY_FAILED,
                brief_id=filter_spec.brief_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        runs = self._rows_to_tuple(tuple(rows))
        logger.debug(
            PERSISTENCE_RESEARCH_RUN_QUERIED,
            brief_id=filter_spec.brief_id,
            count=len(runs),
        )
        return runs

    async def count(self, filter_spec: ResearchRunFilter) -> int:
        """Count runs matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        where_sql, params = _build_query_sql(filter_spec)
        sql = f"SELECT COUNT(*) AS n {where_sql}"
        try:
            cursor = await self._db.execute(sql, params)
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to count research runs"
            logger.warning(
                PERSISTENCE_RESEARCH_RUN_COUNT_FAILED,
                brief_id=filter_spec.brief_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        count = int(row["n"]) if row is not None else 0
        logger.debug(
            PERSISTENCE_RESEARCH_RUN_COUNTED,
            brief_id=filter_spec.brief_id,
            count=count,
        )
        return count

    def _rows_to_tuple(self, rows: Iterable[aiosqlite.Row]) -> tuple[ResearchRun, ...]:
        """Deserialise a row batch with one shared error path.

        Returns:
            The matching collection.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            runs = tuple(_row_to_run(row) for row in rows)
        except (ValueError, ValidationError, KeyError) as exc:
            msg = "Failed to deserialize research runs"
            logger.warning(
                PERSISTENCE_RESEARCH_RUN_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_RESEARCH_RUN_LISTED, count=len(runs))
        return runs


def _build_query_sql(filter_spec: ResearchRunFilter) -> tuple[str, tuple[object, ...]]:
    """Compose the ``FROM ... WHERE`` fragment for ``query`` / ``count``.

    The Postgres repo has its own ``%s``-placeholder twin of this helper.

    Returns:
        ``(sql, params)`` where ``sql`` is the complete query string and ``params`` is
        the matching positional parameter tuple.
    """
    conditions: list[str] = []
    params: list[object] = []
    if filter_spec.brief_id is not None:
        conditions.append("brief_id = ?")
        params.append(filter_spec.brief_id)
    if filter_spec.project_id is not None:
        conditions.append("project_id = ?")
        params.append(filter_spec.project_id)
    if filter_spec.status is not None:
        conditions.append("status = ?")
        params.append(filter_spec.status.value)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return f"FROM research_runs{where}", tuple(params)
