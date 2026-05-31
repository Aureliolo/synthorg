"""SQLite repository for the append-only project-brain store.

Persists every :class:`BrainEntry` revision as a row. A change is a new row
(same ``entry_id``, ``revision`` incremented); current state is the latest
revision per ``entry_id``, computed with a window function.
"""

import json
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.project_brain import (
    BRAIN_PERSIST_COUNT_FAILED,
    BRAIN_PERSIST_DESERIALIZE_FAILED,
    BRAIN_PERSIST_FETCH_FAILED,
    BRAIN_PERSIST_LIST_FAILED,
    BRAIN_PERSIST_PURGE_FAILED,
    BRAIN_PERSIST_QUERY_FAILED,
    BRAIN_PERSIST_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._project_brain_sql import (
    BRAIN_COLUMNS as _COLUMNS,
)
from synthorg.persistence._project_brain_sql import (
    build_current_filter_sql,
    build_filter_sql,
    insert_params,
    row_to_entry,
)
from synthorg.persistence._shared import format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.project_brain.errors import BrainEntryRevisionConflictError
from synthorg.project_brain.models import (
    BrainEntry,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from synthorg.persistence.project_brain_protocol import (
        BrainEntryRevisionKey,
        BrainFilterSpec,
    )
    from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


def _row_to_entry(row: aiosqlite.Row) -> BrainEntry:
    """Reconstruct a :class:`BrainEntry` from a SQLite row.

    Returns:
        The reconstructed entry.
    """
    return row_to_entry(dict(row), load_json=json.loads)


def _insert_params(entity: BrainEntry) -> tuple[object, ...]:
    """Positional INSERT parameters (``recorded_at`` as an ISO string).

    Returns:
        The positional parameter tuple in column order.
    """
    return insert_params(entity, serialize_dt=format_iso_utc)


class SQLiteProjectBrainRepository:
    """SQLite-backed append-only project-brain repository.

    Args:
        db: An open ``aiosqlite`` connection with ``row_factory`` set to
            :class:`aiosqlite.Row`.
        write_context: Async context manager that serialises writes on the
            shared connection.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def _safe_rollback(self, *, event: str) -> None:
        """Roll back the connection, logging if the rollback itself fails."""
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                event,
                error_type=type(rollback_exc).__name__,
                error=safe_error_description(rollback_exc),
                rollback_failed=True,
            )

    async def append(self, event: BrainEntry) -> None:
        """Append one entry revision with a precomputed revision.

        Raises:
            BrainEntryRevisionConflictError: If ``(entry_id, revision)`` exists.
            QueryError: If the database query fails.
        """
        sql = (
            f"INSERT INTO project_brain_entries ({_COLUMNS}) "  # noqa: S608
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        async with self._write_context():
            try:
                await self._db.execute(sql, _insert_params(event))
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await self._safe_rollback(event=BRAIN_PERSIST_SAVE_FAILED)
                msg = (
                    f"Brain revision conflict for {event.entry_id!r}"
                    f" rev {event.revision}"
                )
                logger.warning(
                    BRAIN_PERSIST_SAVE_FAILED,
                    entry_id=event.entry_id,
                    revision=event.revision,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise BrainEntryRevisionConflictError(msg) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(event=BRAIN_PERSIST_SAVE_FAILED)
                msg = f"Failed to append brain entry {event.entry_id!r}"
                logger.warning(
                    BRAIN_PERSIST_SAVE_FAILED,
                    entry_id=event.entry_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def append_with_next_revision(self, entry: BrainEntry) -> BrainEntry:
        """Append ``entry`` at the next revision for its ``entry_id`` (atomic).

        Returns:
            The persisted entry with the server-assigned ``revision``.

        Raises:
            BrainEntryRevisionConflictError: If a concurrent writer won the race.
            QueryError: If the database query fails.
        """
        next_rev_sql = (
            "SELECT COALESCE(MAX(revision), 0) + 1 FROM project_brain_entries "
            "WHERE project_id = ? AND entry_id = ?"
        )
        insert_sql = (
            f"INSERT INTO project_brain_entries ({_COLUMNS}) "  # noqa: S608
            f"VALUES (?, ?, ({next_rev_sql}), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        full = _insert_params(entry)
        # full[2] is the placeholder revision; the subquery computes the real
        # value, so we splice (project_id, entry_id) in for it instead.
        params = (full[0], full[1], full[0], full[1], *full[3:])
        async with self._write_context():
            try:
                await self._db.execute(insert_sql, params)
                cursor = await self._db.execute(
                    "SELECT MAX(revision) AS rev FROM project_brain_entries "
                    "WHERE project_id = ? AND entry_id = ?",
                    (entry.project_id, entry.entry_id),
                )
                row = await cursor.fetchone()
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await self._safe_rollback(event=BRAIN_PERSIST_SAVE_FAILED)
                msg = f"Brain revision race for entry {entry.entry_id!r}"
                logger.warning(
                    BRAIN_PERSIST_SAVE_FAILED,
                    entry_id=entry.entry_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise BrainEntryRevisionConflictError(msg) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(event=BRAIN_PERSIST_SAVE_FAILED)
                msg = f"Failed to append brain entry {entry.entry_id!r}"
                logger.warning(
                    BRAIN_PERSIST_SAVE_FAILED,
                    entry_id=entry.entry_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        if row is None or row["rev"] is None:
            msg = f"Revision readback failed for brain entry {entry.entry_id!r}"
            logger.error(BRAIN_PERSIST_SAVE_FAILED, entry_id=entry.entry_id, error=msg)
            raise QueryError(msg)
        return entry.model_copy(update={"revision": int(row["rev"])})

    async def get(self, entity_id: BrainEntryRevisionKey) -> BrainEntry | None:
        """Retrieve one exact revision.

        Returns:
            The matching entry, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        project_id, entry_id, revision = entity_id
        sql = (
            f"SELECT {_COLUMNS} FROM project_brain_entries "  # noqa: S608
            "WHERE project_id = ? AND entry_id = ? AND revision = ?"
        )
        row = await self._fetch_one(sql, (project_id, entry_id, revision))
        return _row_to_entry(row) if row is not None else None

    async def get_current(
        self,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
    ) -> BrainEntry | None:
        """Retrieve the latest revision of one entry.

        Returns:
            The latest entry revision, or ``None`` when absent.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            f"SELECT {_COLUMNS} FROM project_brain_entries "  # noqa: S608
            "WHERE project_id = ? AND entry_id = ? "
            "ORDER BY revision DESC LIMIT 1"
        )
        row = await self._fetch_one(sql, (project_id, entry_id))
        return _row_to_entry(row) if row is not None else None

    async def history(
        self,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BrainEntry, ...]:
        """Return one entry's revision chain, oldest-first.

        Returns:
            The entry's revisions, oldest-first.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=BRAIN_PERSIST_QUERY_FAILED
        )
        sql = (
            f"SELECT {_COLUMNS} FROM project_brain_entries "  # noqa: S608
            "WHERE project_id = ? AND entry_id = ? "
            "ORDER BY revision ASC LIMIT ? OFFSET ?"
        )
        params = (project_id, entry_id, min(limit, _MAX_LIST_ROWS), offset)
        return await self._fetch_many(sql, params, event=BRAIN_PERSIST_QUERY_FAILED)

    async def query(
        self,
        filter_spec: BrainFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BrainEntry, ...]:
        """Return every matching revision row, newest-first.

        Returns:
            Matching revisions, newest-first by ``recorded_at``.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=BRAIN_PERSIST_QUERY_FAILED
        )
        where_sql, params = _filter_sql(filter_spec)
        sql = (
            f"SELECT {_COLUMNS} FROM project_brain_entries {where_sql} "  # noqa: S608
            "ORDER BY recorded_at DESC, revision DESC LIMIT ? OFFSET ?"
        )
        params = (*params, min(limit, _MAX_LIST_ROWS), offset)
        return await self._fetch_many(sql, params, event=BRAIN_PERSIST_QUERY_FAILED)

    async def list_current(
        self,
        filter_spec: BrainFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BrainEntry, ...]:
        """Return the current-state projection (latest revision per entry).

        Returns:
            Current-state entries matching the filter, newest-first.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(limit, offset, event=BRAIN_PERSIST_LIST_FAILED)
        outer_where, params = _current_filter_sql(filter_spec)
        sql = (
            f"SELECT {_COLUMNS} FROM ("  # noqa: S608
            f"SELECT *, ROW_NUMBER() OVER ("
            "PARTITION BY entry_id ORDER BY revision DESC) AS rn "
            "FROM project_brain_entries WHERE project_id = ?"
            f") WHERE rn = 1{outer_where} "
            "ORDER BY recorded_at DESC, entry_id ASC LIMIT ? OFFSET ?"
        )
        params = (filter_spec.project_id, *params, min(limit, _MAX_LIST_ROWS), offset)
        return await self._fetch_many(sql, params, event=BRAIN_PERSIST_LIST_FAILED)

    async def count(self, filter_spec: BrainFilterSpec) -> int:
        """Count current-state entries matching the filter.

        Returns:
            Number of current-state entries that match.

        Raises:
            QueryError: If the database query fails.
        """
        outer_where, params = _current_filter_sql(filter_spec)
        sql = (
            "SELECT COUNT(*) AS n FROM ("  # noqa: S608
            "SELECT entry_id, status, entry_kind, author, recorded_at, tags, "
            "related_task_ids, ROW_NUMBER() OVER ("
            "PARTITION BY entry_id ORDER BY revision DESC) AS rn "
            "FROM project_brain_entries WHERE project_id = ?"
            f") WHERE rn = 1{outer_where}"
        )
        params = (filter_spec.project_id, *params)
        try:
            cursor = await self._db.execute(sql, params)
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to count brain entries for {filter_spec.project_id!r}"
            logger.warning(
                BRAIN_PERSIST_COUNT_FAILED,
                project_id=filter_spec.project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row["n"]) if row is not None else 0

    async def mark_indexed(
        self,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        revision: int,
    ) -> None:
        """Upsert the last-indexed revision for one entry.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            "INSERT INTO project_brain_index_state "
            "(project_id, entry_id, last_indexed_revision) VALUES (?, ?, ?) "
            "ON CONFLICT(project_id, entry_id) DO UPDATE SET "
            "last_indexed_revision = excluded.last_indexed_revision "
            "WHERE excluded.last_indexed_revision "
            "> project_brain_index_state.last_indexed_revision"
        )
        async with self._write_context():
            try:
                await self._db.execute(sql, (project_id, entry_id, revision))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(event=BRAIN_PERSIST_SAVE_FAILED)
                msg = f"Failed to mark brain entry {entry_id!r} indexed"
                logger.warning(
                    BRAIN_PERSIST_SAVE_FAILED,
                    entry_id=entry_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def indexed_revisions(
        self,
        project_id: NotBlankStr,
    ) -> dict[NotBlankStr, int]:
        """Return the last-indexed revision per entry for a project.

        Returns:
            Mapping of ``entry_id`` to last-indexed revision.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            "SELECT entry_id, last_indexed_revision FROM project_brain_index_state "
            "WHERE project_id = ?"
        )
        try:
            cursor = await self._db.execute(sql, (project_id,))
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to read brain index state for {project_id!r}"
            logger.warning(
                BRAIN_PERSIST_QUERY_FAILED,
                project_id=project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return {
            NotBlankStr(row["entry_id"]): int(row["last_indexed_revision"])
            for row in rows
        }

    async def purge_before(self, threshold: datetime) -> int:
        """Purge superseded revisions older than ``threshold`` (retention).

        Always retains the latest revision of every entry, so current state
        survives any sweep.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            "DELETE FROM project_brain_entries "
            "WHERE recorded_at < ? AND EXISTS ("
            "SELECT 1 FROM project_brain_entries AS later "
            "WHERE later.project_id = project_brain_entries.project_id "
            "AND later.entry_id = project_brain_entries.entry_id "
            "AND later.revision > project_brain_entries.revision)"
        )
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, (format_iso_utc(threshold),))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(event=BRAIN_PERSIST_PURGE_FAILED)
                msg = "Failed to purge brain entries"
                logger.warning(
                    BRAIN_PERSIST_PURGE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return max(0, cursor.rowcount)

    async def _fetch_one(
        self, sql: str, params: tuple[object, ...]
    ) -> aiosqlite.Row | None:
        """Run a single-row SELECT.

        Returns:
            The row, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            cursor = await self._db.execute(sql, params)
            return await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to fetch brain entry"
            logger.warning(
                BRAIN_PERSIST_FETCH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def _fetch_many(
        self, sql: str, params: tuple[object, ...], *, event: str
    ) -> tuple[BrainEntry, ...]:
        """Run a multi-row SELECT and deserialise the batch.

        Returns:
            The reconstructed entries.

        Raises:
            QueryError: If the database query or deserialisation fails.
        """
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query brain entries"
            logger.warning(
                event,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return self._rows_to_tuple(rows)

    def _rows_to_tuple(self, rows: Iterable[aiosqlite.Row]) -> tuple[BrainEntry, ...]:
        """Deserialise a row batch with one shared error path.

        Returns:
            The reconstructed entries.

        Raises:
            QueryError: If deserialisation fails.
        """
        try:
            return tuple(_row_to_entry(row) for row in rows)
        except (ValueError, ValidationError, KeyError, json.JSONDecodeError) as exc:
            msg = "Failed to deserialize brain entries"
            logger.warning(
                BRAIN_PERSIST_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc


def _filter_sql(filter_spec: BrainFilterSpec) -> tuple[str, tuple[object, ...]]:
    """All-revisions ``WHERE`` clause for this backend (``?`` placeholders).

    Returns:
        ``(where_sql, params)`` with ``project_id`` first.
    """
    return build_filter_sql(filter_spec, ph="?", serialize_dt=format_iso_utc)


def _current_filter_sql(
    filter_spec: BrainFilterSpec,
) -> tuple[str, tuple[object, ...]]:
    """Outer AND-fragment for current-state queries (``?`` placeholders).

    Returns:
        ``(outer_and_sql, params)`` beginning with `` AND`` when non-empty.
    """
    return build_current_filter_sql(filter_spec, ph="?", serialize_dt=format_iso_utc)
