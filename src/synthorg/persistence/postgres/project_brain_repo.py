"""Postgres repository for the append-only project-brain store.

Mirror of the SQLite implementation: every :class:`BrainEntry` revision is a
row, current state is the latest revision per ``entry_id`` (window function),
and a change is a new row rather than an in-place update.
"""

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
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
from synthorg.persistence._shared import coerce_row_timestamp
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.project_brain.errors import BrainEntryRevisionConflictError
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.persistence.project_brain_protocol import (
        BrainEntryRevisionKey,
        BrainFilterSpec,
    )

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000

_COLUMNS = (
    "project_id, entry_id, revision, entry_kind, title, rationale, status, "
    "author, recorded_at, related_task_ids, related_entry_ids, "
    "supersedes_entry_id, tags, confidence, citations, payload"
)


def _row_to_entry(row: dict[str, Any]) -> BrainEntry:
    """Reconstruct a :class:`BrainEntry` from a Postgres ``dict_row``.

    Returns:
        The reconstructed entry.
    """
    data = dict(row)
    data.pop("rn", None)
    data["entry_kind"] = BrainEntryKind(data["entry_kind"])
    data["status"] = BrainEntryStatus(data["status"])
    data["tags"] = tuple(_load_json_list(data["tags"]))
    data["related_task_ids"] = tuple(_load_json_list(data["related_task_ids"]))
    data["related_entry_ids"] = tuple(_load_json_list(data["related_entry_ids"]))
    data["citations"] = _load_json(data["citations"])
    data["payload"] = _load_json(data["payload"])
    data["recorded_at"] = coerce_row_timestamp(data["recorded_at"])
    return BrainEntry.model_validate(data)


def _load_json(value: object) -> Any:
    """Parse a JSON column that Postgres may return as ``str`` or pre-parsed.

    Returns:
        The decoded JSON value.
    """
    if isinstance(value, str):
        return json.loads(value)
    return value


def _load_json_list(value: object) -> list[Any]:
    """Parse a JSON-array column into a list.

    Returns:
        The decoded list.
    """
    parsed = _load_json(value)
    return list(parsed)


def _insert_params(entity: BrainEntry) -> tuple[object, ...]:
    """Scalar SQL parameters for a full-row INSERT (revision included).

    Returns:
        The positional parameter tuple in column order.
    """
    return (
        entity.project_id,
        entity.entry_id,
        entity.revision,
        entity.entry_kind.value,
        entity.title,
        entity.rationale,
        entity.status.value,
        entity.author,
        entity.recorded_at,
        json.dumps(list(entity.related_task_ids)),
        json.dumps(list(entity.related_entry_ids)),
        entity.supersedes_entry_id,
        json.dumps(list(entity.tags), sort_keys=True),
        entity.confidence,
        json.dumps([c.model_dump(mode="json") for c in entity.citations]),
        json.dumps(entity.payload.model_dump(mode="json"), sort_keys=True),
    )


def _escape_like(value: str) -> str:
    r"""Escape LIKE metacharacters so a JSON needle matches literally.

    Returns:
        The escaped value (paired with ``ESCAPE '\'`` in the query).
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class PostgresProjectBrainRepository:
    """Postgres-backed append-only project-brain repository."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def _safe_rollback(
        self, conn: psycopg.AsyncConnection[Any], *, event: str
    ) -> None:
        """Roll back the connection, logging if the rollback itself fails."""
        try:
            await conn.rollback()
        except psycopg.Error as rollback_exc:
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
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        async with self._pool.connection() as conn, conn.cursor() as cur:
            try:
                await cur.execute(sql, _insert_params(event))
                await conn.commit()
            except psycopg.errors.UniqueViolation as exc:
                await self._safe_rollback(conn, event=BRAIN_PERSIST_SAVE_FAILED)
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
            except psycopg.Error as exc:
                await self._safe_rollback(conn, event=BRAIN_PERSIST_SAVE_FAILED)
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
            "WHERE project_id = %s AND entry_id = %s"
        )
        insert_sql = (
            f"INSERT INTO project_brain_entries ({_COLUMNS}) "  # noqa: S608
            f"VALUES (%s, %s, ({next_rev_sql}), %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s) RETURNING revision"
        )
        full = _insert_params(entry)
        params = (full[0], full[1], full[0], full[1], *full[3:])
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            try:
                await cur.execute(insert_sql, params)
                row = await cur.fetchone()
                await conn.commit()
            except psycopg.errors.UniqueViolation as exc:
                await self._safe_rollback(conn, event=BRAIN_PERSIST_SAVE_FAILED)
                msg = f"Brain revision race for entry {entry.entry_id!r}"
                logger.warning(
                    BRAIN_PERSIST_SAVE_FAILED,
                    entry_id=entry.entry_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise BrainEntryRevisionConflictError(msg) from exc
            except psycopg.Error as exc:
                await self._safe_rollback(conn, event=BRAIN_PERSIST_SAVE_FAILED)
                msg = f"Failed to append brain entry {entry.entry_id!r}"
                logger.warning(
                    BRAIN_PERSIST_SAVE_FAILED,
                    entry_id=entry.entry_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        assigned = int(row["revision"]) if row is not None else entry.revision
        return entry.model_copy(update={"revision": assigned})

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
            "WHERE project_id = %s AND entry_id = %s AND revision = %s"
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
            "WHERE project_id = %s AND entry_id = %s "
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
            "WHERE project_id = %s AND entry_id = %s "
            "ORDER BY revision ASC LIMIT %s OFFSET %s"
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
        where_sql, params = _build_filter_sql(filter_spec)
        sql = (
            f"SELECT {_COLUMNS} FROM project_brain_entries {where_sql} "  # noqa: S608
            "ORDER BY recorded_at DESC, revision DESC LIMIT %s OFFSET %s"
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
        outer_where, params = _build_current_filter_sql(filter_spec)
        sql = (
            f"SELECT {_COLUMNS} FROM ("  # noqa: S608
            "SELECT *, ROW_NUMBER() OVER ("
            "PARTITION BY entry_id ORDER BY revision DESC) AS rn "
            "FROM project_brain_entries WHERE project_id = %s"
            f") sub WHERE rn = 1{outer_where} "
            "ORDER BY recorded_at DESC, entry_id ASC LIMIT %s OFFSET %s"
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
        outer_where, params = _build_current_filter_sql(filter_spec)
        sql = (
            "SELECT COUNT(*) AS n FROM ("  # noqa: S608
            "SELECT entry_id, status, entry_kind, author, recorded_at, tags, "
            "related_task_ids, ROW_NUMBER() OVER ("
            "PARTITION BY entry_id ORDER BY revision DESC) AS rn "
            "FROM project_brain_entries WHERE project_id = %s"
            f") sub WHERE rn = 1{outer_where}"
        )
        params = (filter_spec.project_id, *params)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to count brain entries for {filter_spec.project_id!r}"
            logger.warning(
                BRAIN_PERSIST_COUNT_FAILED,
                project_id=filter_spec.project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row["n"]) if row is not None else 0

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
            "WHERE recorded_at < %s AND (project_id, entry_id, revision) NOT IN ("
            "SELECT project_id, entry_id, MAX(revision) FROM project_brain_entries "
            "GROUP BY project_id, entry_id)"
        )
        async with self._pool.connection() as conn, conn.cursor() as cur:
            try:
                await cur.execute(sql, (threshold,))
                removed = max(0, cur.rowcount)
                await conn.commit()
            except psycopg.Error as exc:
                await self._safe_rollback(conn, event=BRAIN_PERSIST_PURGE_FAILED)
                msg = "Failed to purge brain entries"
                logger.warning(
                    BRAIN_PERSIST_PURGE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return removed

    async def _fetch_one(
        self, sql: str, params: tuple[object, ...]
    ) -> dict[str, Any] | None:
        """Run a single-row SELECT.

        Returns:
            The row dict, or ``None``.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                return await cur.fetchone()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query brain entries"
            logger.warning(
                event,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return self._rows_to_tuple(tuple(rows))

    def _rows_to_tuple(
        self, rows: tuple[dict[str, Any], ...]
    ) -> tuple[BrainEntry, ...]:
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


def _filter_conditions(
    filter_spec: BrainFilterSpec,
) -> tuple[list[str], list[object]]:
    """Build the shared AND-conditions (kind/status/tag/author/task/since).

    Returns:
        ``(conditions, params)`` excluding the leading ``project_id`` predicate.
    """
    conditions: list[str] = []
    params: list[object] = []
    if filter_spec.entry_kind is not None:
        conditions.append("entry_kind = %s")
        params.append(filter_spec.entry_kind.value)
    if filter_spec.status is not None:
        conditions.append("status = %s")
        params.append(filter_spec.status.value)
    if filter_spec.author is not None:
        conditions.append("author = %s")
        params.append(filter_spec.author)
    if filter_spec.tag is not None:
        conditions.append("tags LIKE %s ESCAPE '\\'")
        params.append(f'%"{_escape_like(filter_spec.tag)}"%')
    if filter_spec.related_task_id is not None:
        conditions.append("related_task_ids LIKE %s ESCAPE '\\'")
        params.append(f'%"{_escape_like(filter_spec.related_task_id)}"%')
    if filter_spec.updated_since is not None:
        conditions.append("recorded_at >= %s")
        params.append(filter_spec.updated_since)
    return conditions, params


def _build_filter_sql(filter_spec: BrainFilterSpec) -> tuple[str, tuple[object, ...]]:
    """Compose the ``WHERE`` clause for :meth:`query` (all revisions).

    Returns:
        ``(where_sql, params)`` with ``project_id`` first.
    """
    conditions, params = _filter_conditions(filter_spec)
    sql = "WHERE project_id = %s"
    full_params: list[object] = [filter_spec.project_id, *params]
    if conditions:
        sql += " AND " + " AND ".join(conditions)
    return sql, tuple(full_params)


def _build_current_filter_sql(
    filter_spec: BrainFilterSpec,
) -> tuple[str, tuple[object, ...]]:
    """Compose the outer AND-fragment for current-state queries.

    Returns:
        ``(outer_and_sql, params)`` where ``outer_and_sql`` begins with `` AND``
        when non-empty.
    """
    conditions, params = _filter_conditions(filter_spec)
    sql = (" AND " + " AND ".join(conditions)) if conditions else ""
    return sql, tuple(params)
