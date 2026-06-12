"""SQLite repository implementation for :class:`DocMetadata`.

Persists the lightweight metadata projection used by the wiki list view
and the on-boot reindex job. Body bytes live in the project git
workspace; this row only carries pointers + indexing state.
"""

import json
import sqlite3
from collections.abc import Iterable

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.docs_engine.enums import DocType
from synthorg.docs_engine.models import DocMetadata
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.project_doc import (
    PERSISTENCE_PROJECT_DOC_COUNT_FAILED,
    PERSISTENCE_PROJECT_DOC_COUNTED,
    PERSISTENCE_PROJECT_DOC_DELETE_FAILED,
    PERSISTENCE_PROJECT_DOC_DESERIALIZE_FAILED,
    PERSISTENCE_PROJECT_DOC_FETCH_FAILED,
    PERSISTENCE_PROJECT_DOC_FETCHED,
    PERSISTENCE_PROJECT_DOC_LIST_FAILED,
    PERSISTENCE_PROJECT_DOC_LISTED,
    PERSISTENCE_PROJECT_DOC_QUERIED,
    PERSISTENCE_PROJECT_DOC_QUERY_FAILED,
    PERSISTENCE_PROJECT_DOC_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.docs_protocol import DocsFilterSpec, DocsRepositoryKey
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


def _row_to_metadata(row: aiosqlite.Row) -> DocMetadata:
    """Reconstruct a :class:`DocMetadata` from a database row.

    Returns:
        Result of type ``DocMetadata``.
    """
    data = dict(row)
    data["doc_type"] = DocType(data["doc_type"])
    data["tags"] = tuple(json.loads(data["tags"]))
    data["related_task_ids"] = tuple(json.loads(data["related_task_ids"]))
    data["created_at"] = coerce_row_timestamp(data["created_at"])
    data["updated_at"] = coerce_row_timestamp(data["updated_at"])
    return DocMetadata.model_validate(data)


class SQLiteDocsRepository:
    """SQLite-backed living-doc metadata repository.

    Args:
        db: An open ``aiosqlite`` connection with ``row_factory`` set to
            :class:`aiosqlite.Row`.
        write_context: Async context manager that serialises writes on
            the shared connection.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    @staticmethod
    def _row_params(entity: DocMetadata) -> tuple[object, ...]:
        """Row params.

        Returns:
            Tuple of scalar SQL parameter values for INSERT/UPDATE.
        """
        return (
            entity.project_id,
            entity.slug,
            entity.doc_type.value,
            entity.title,
            json.dumps(list(entity.tags), sort_keys=True),
            json.dumps(list(entity.related_task_ids), sort_keys=True),
            entity.head_commit_sha,
            entity.last_indexed_commit_sha,
            format_iso_utc(entity.created_at),
            format_iso_utc(entity.updated_at),
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

    async def save(self, entity: DocMetadata) -> None:
        """Persist doc metadata via upsert.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    """\
INSERT INTO project_docs (project_id, slug, doc_type, title, tags,
                          related_task_ids, head_commit_sha,
                          last_indexed_commit_sha, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(project_id, slug) DO UPDATE SET
    doc_type=excluded.doc_type,
    title=excluded.title,
    tags=excluded.tags,
    related_task_ids=excluded.related_task_ids,
    head_commit_sha=excluded.head_commit_sha,
    last_indexed_commit_sha=excluded.last_indexed_commit_sha,
    created_at=excluded.created_at,
    updated_at=excluded.updated_at""",
                    self._row_params(entity),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(event=PERSISTENCE_PROJECT_DOC_SAVE_FAILED)
                msg = f"Failed to save living doc {entity.project_id!r}/{entity.slug!r}"
                logger.warning(
                    PERSISTENCE_PROJECT_DOC_SAVE_FAILED,
                    project_id=entity.project_id,
                    slug=entity.slug,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: DocsRepositoryKey) -> DocMetadata | None:
        """Retrieve doc metadata by ``(project_id, slug)``.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        project_id, slug = entity_id
        try:
            cursor = await self._db.execute(
                """SELECT * FROM project_docs
                   WHERE project_id = ? AND slug = ?""",
                (project_id, slug),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch living doc {project_id!r}/{slug!r}"
            logger.warning(
                PERSISTENCE_PROJECT_DOC_FETCH_FAILED,
                project_id=project_id,
                slug=slug,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(
                PERSISTENCE_PROJECT_DOC_FETCHED,
                project_id=project_id,
                slug=slug,
                found=False,
            )
            return None
        try:
            metadata = _row_to_metadata(row)
        except (ValueError, ValidationError, KeyError, json.JSONDecodeError) as exc:
            msg = f"Failed to deserialize living doc {project_id!r}/{slug!r}"
            logger.warning(
                PERSISTENCE_PROJECT_DOC_DESERIALIZE_FAILED,
                project_id=project_id,
                slug=slug,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_PROJECT_DOC_FETCHED,
            project_id=project_id,
            slug=slug,
            found=True,
        )
        return metadata

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DocMetadata, ...]:
        """List all doc metadata, recency-first.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PROJECT_DOC_LIST_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        try:
            cursor = await self._db.execute(
                """SELECT * FROM project_docs
                   ORDER BY updated_at DESC, project_id ASC, slug ASC
                   LIMIT ? OFFSET ?""",
                (effective_limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list living docs"
            logger.warning(
                PERSISTENCE_PROJECT_DOC_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return self._rows_to_tuple(tuple(rows))

    async def delete(self, entity_id: DocsRepositoryKey) -> bool:
        """Delete doc metadata by ``(project_id, slug)``.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        project_id, slug = entity_id
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM project_docs WHERE project_id = ? AND slug = ?",
                    (project_id, slug),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(event=PERSISTENCE_PROJECT_DOC_DELETE_FAILED)
                msg = f"Failed to delete living doc {project_id!r}/{slug!r}"
                logger.warning(
                    PERSISTENCE_PROJECT_DOC_DELETE_FAILED,
                    project_id=project_id,
                    slug=slug,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return cursor.rowcount > 0

    async def query(
        self,
        filter_spec: DocsFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[DocMetadata, ...]:
        """Return docs matching the filter spec, recency-first.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PROJECT_DOC_QUERY_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        where_sql, params = _build_query_sql(filter_spec)
        sql = (
            f"SELECT * {where_sql} ORDER BY updated_at DESC, slug ASC LIMIT ? OFFSET ?"
        )
        params = (*params, effective_limit, offset)
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to query living docs for project {filter_spec.project_id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_DOC_QUERY_FAILED,
                project_id=filter_spec.project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        metadata = self._rows_to_tuple(tuple(rows))
        logger.debug(
            PERSISTENCE_PROJECT_DOC_QUERIED,
            project_id=filter_spec.project_id,
            doc_type=filter_spec.doc_type.value if filter_spec.doc_type else None,
            tag=filter_spec.tag,
            count=len(metadata),
        )
        return metadata

    async def count(self, filter_spec: DocsFilterSpec) -> int:
        """Count docs matching the filter spec.

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
            msg = f"Failed to count living docs for {filter_spec.project_id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_DOC_COUNT_FAILED,
                project_id=filter_spec.project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        count = int(row["n"]) if row is not None else 0
        logger.debug(
            PERSISTENCE_PROJECT_DOC_COUNTED,
            project_id=filter_spec.project_id,
            count=count,
        )
        return count

    def _rows_to_tuple(self, rows: Iterable[aiosqlite.Row]) -> tuple[DocMetadata, ...]:
        """Deserialise a row batch with one shared error path.

        Returns:
            The matching collection.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            metadata = tuple(_row_to_metadata(row) for row in rows)
        except (ValueError, ValidationError, KeyError, json.JSONDecodeError) as exc:
            msg = "Failed to deserialize living docs"
            logger.warning(
                PERSISTENCE_PROJECT_DOC_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_PROJECT_DOC_LISTED, count=len(metadata))
        return metadata


def _escape_like(value: str) -> str:
    r"""Escape LIKE metacharacters so a tag matches literally.

    Without this a tag containing ``%`` or ``_`` would behave as a
    wildcard. Backslash is escaped first, then the wildcards; the query
    pairs this with ``ESCAPE '\'``.

    Returns:
        Result of type ``str``.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_query_sql(filter_spec: DocsFilterSpec) -> tuple[str, tuple[object, ...]]:
    """Compose the ``FROM ... WHERE`` fragment for ``query`` / ``count``.

    Returns the fragment without a SELECT head so both callers can
    prepend their own (``SELECT *`` vs ``SELECT COUNT(*)``).

    Tag filtering uses ``LIKE`` against the JSON-encoded tag list. The
    needle is the ``json.dumps`` of the tag (its quoted, JSON-escaped
    form), so a tag containing quote characters matches its stored
    representation, and the surrounding quotes prevent partial-substring
    false matches (e.g. tag ``a`` cannot match a stored tag ``ab``).

    Returns:
        ``(sql, params)`` where ``sql`` is the ``FROM ... WHERE ...`` fragment
        (callers prepend their own ``SELECT`` clause) and ``params`` is the
        matching positional parameter tuple.
    """
    sql = "FROM project_docs WHERE project_id = ?"
    params: list[object] = [filter_spec.project_id]
    if filter_spec.doc_type is not None:
        sql += " AND doc_type = ?"
        params.append(filter_spec.doc_type.value)
    if filter_spec.tag is not None:
        sql += " AND tags LIKE ? ESCAPE '\\'"
        needle = json.dumps(filter_spec.tag)
        params.append(f"%{_escape_like(needle)}%")
    if filter_spec.related_task_id is not None:
        # Membership test against the JSON-array column, same quoted-needle
        # LIKE technique as ``tag`` so the surrounding quotes prevent a
        # partial-id false match (id ``t1`` cannot match stored ``t12``).
        sql += " AND related_task_ids LIKE ? ESCAPE '\\'"
        task_needle = json.dumps(filter_spec.related_task_id)
        params.append(f"%{_escape_like(task_needle)}%")
    if filter_spec.updated_since is not None:
        sql += " AND updated_at >= ?"
        params.append(format_iso_utc(filter_spec.updated_since))
    return sql, tuple(params)
