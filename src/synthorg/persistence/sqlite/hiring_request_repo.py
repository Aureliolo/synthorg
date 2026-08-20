"""SQLite repository for in-flight hiring requests.

The nested ``HiringRequest`` round-trips through a single JSON
``payload`` column; ``status`` / ``requested_by`` / ``department`` /
``role`` / ``created_at`` are promoted to columns for filtering and
recency ordering. Upsert-by-id keyed on the request id.
"""

import json
import sqlite3

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.hr.models import HiringRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.hiring_request import (
    PERSISTENCE_HIRING_REQUEST_QUERIED,
    PERSISTENCE_HIRING_REQUEST_QUERY_FAILED,
    PERSISTENCE_HIRING_REQUEST_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence._shared.datetime_marshaller import format_iso_utc
from synthorg.persistence.hiring_request_protocol import (
    HiringRequestFilterSpec,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

# Conflict target is the primary key alone, deliberately. ``INSERT OR REPLACE``
# resolves a conflict on ANY unique index by DELETING the row it collided with,
# so under the one-open-hire-per-role index a second open request for a role
# would silently destroy the first rather than being refused. Postgres targets
# ``id`` for the same reason; matching it keeps the two backends answering the
# same way instead of one raising while the other loses a row.
_UPSERT_SQL = """
INSERT INTO hiring_requests (
    id, status, requested_by, department, role, created_at, payload
)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id) DO UPDATE SET
    status = excluded.status,
    requested_by = excluded.requested_by,
    department = excluded.department,
    role = excluded.role,
    created_at = excluded.created_at,
    payload = excluded.payload
"""
_SELECT_SQL = "SELECT payload FROM hiring_requests"


def _to_params(request: HiringRequest) -> tuple[object, ...]:
    """Marshal a ``HiringRequest`` into positional INSERT params.

    Returns:
        Positional params in column order.
    """
    return (
        str(request.id),
        request.status.value,
        str(request.requested_by),
        str(request.department),
        str(request.role),
        format_iso_utc(request.created_at),
        json.dumps(request.model_dump(mode="json"), sort_keys=True),
    )


def _row_to_request(payload: object) -> HiringRequest:
    """Deserialise a JSON ``payload`` column into a ``HiringRequest``.

    Returns:
        The reconstructed ``HiringRequest``.

    Raises:
        QueryError: If the payload is not a JSON object or fails validation.
    """
    data = json.loads(str(payload)) if payload else {}
    if not isinstance(data, dict):
        msg = f"hiring_requests.payload is not a JSON object: {data!r}"
        raise QueryError(msg)
    try:
        return HiringRequest.model_validate(data)
    except ValidationError as exc:
        msg = f"corrupt hiring_requests payload: {data!r}"
        raise QueryError(msg) from exc


class SQLiteHiringRequestRepository:
    """SQLite CRUD + status-filtered query for hiring requests.

    Args:
        db: An open ``aiosqlite.Connection``.
        write_context: Shared backend write context.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def save(self, entity: HiringRequest, /) -> None:
        """Upsert one hiring request keyed on its id.

        Raises:
            QueryError: If the write fails.
        """
        async with self._write_context():
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                await self._db.execute(_UPSERT_SQL, _to_params(entity))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = "Failed to save hiring request"
                logger.warning(
                    PERSISTENCE_HIRING_REQUEST_SAVE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    request_id=str(entity.id),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr, /) -> HiringRequest | None:
        """Return the hiring request with ``entity_id`` or ``None``.

        Returns:
            The request, or ``None`` when absent.

        Raises:
            QueryError: If the read fails.
        """
        try:
            async with self._db.execute(
                f"{_SELECT_SQL} WHERE id = ?",
                (str(entity_id),),
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            raise self._read_error(exc) from exc
        if row is None:
            return None
        return self._deserialize(dict(row)["payload"])

    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete the hiring request with ``entity_id``.

        Returns:
            ``True`` iff a row existed.

        Raises:
            QueryError: If the delete fails.
        """
        async with self._write_context():
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                async with self._db.execute(
                    "DELETE FROM hiring_requests WHERE id = ?",
                    (str(entity_id),),
                ) as cursor:
                    removed = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = "Failed to delete hiring request"
                logger.warning(
                    PERSISTENCE_HIRING_REQUEST_SAVE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    request_id=str(entity_id),
                )
                raise QueryError(msg) from exc
        return removed > 0

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[HiringRequest, ...]:
        """List hiring requests, newest-first by ``created_at``.

        Returns:
            Requests, paginated.

        Raises:
            QueryError: If the read fails.
        """
        return await self._query_rows(None, limit=limit, offset=offset)

    async def query(
        self,
        filter_spec: HiringRequestFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[HiringRequest, ...]:
        """Return requests matching the filter, newest-first.

        Returns:
            Matching requests, newest-first.

        Raises:
            QueryError: If the read fails.
        """
        return await self._query_rows(filter_spec, limit=limit, offset=offset)

    async def count(self, filter_spec: HiringRequestFilterSpec) -> int:
        """Return the number of requests matching the filter.

        Returns:
            The match count.

        Raises:
            QueryError: If the read fails.
        """
        clause, params = _where(filter_spec)
        sql = (
            "SELECT COUNT(*) AS cnt FROM hiring_requests"  # noqa: S608
            + clause
        )
        try:
            async with self._db.execute(sql, params) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            raise self._read_error(exc) from exc
        return int(dict(row)["cnt"]) if row else 0

    async def _query_rows(
        self,
        filter_spec: HiringRequestFilterSpec | None,
        *,
        limit: int,
        offset: int,
    ) -> tuple[HiringRequest, ...]:
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_HIRING_REQUEST_QUERY_FAILED
        )
        clause, params = _where(filter_spec)
        sql = (
            _SELECT_SQL + clause + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        )
        params = [*params, limit, offset]
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = list(await cursor.fetchall())
        except (sqlite3.Error, aiosqlite.Error) as exc:
            raise self._read_error(exc) from exc
        try:
            requests = tuple(self._deserialize(dict(r)["payload"]) for r in rows)
        except QueryError:
            raise
        except Exception as exc:
            msg = "corrupt hiring_requests row(s)"
            logger.warning(
                PERSISTENCE_HIRING_REQUEST_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_HIRING_REQUEST_QUERIED, count=len(requests))
        return requests

    def _deserialize(self, payload: object) -> HiringRequest:
        return _row_to_request(payload)

    def _read_error(self, exc: Exception) -> QueryError:
        msg = "Failed to query hiring requests"
        logger.warning(
            PERSISTENCE_HIRING_REQUEST_QUERY_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return QueryError(msg)

    async def _safe_rollback(self) -> None:
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                PERSISTENCE_HIRING_REQUEST_SAVE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                rollback_failed=True,
            )


def _where(
    filter_spec: HiringRequestFilterSpec | None,
) -> tuple[str, list[object]]:
    """Build the WHERE clause + params for an optional filter.

    Returns:
        ``(clause, params)`` where ``clause`` is empty or ``" WHERE ..."``.
    """
    if filter_spec is None:
        return "", []
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.status is not None:
        clauses.append("status = ?")
        params.append(filter_spec.status.value)
    if filter_spec.requested_by is not None:
        clauses.append("requested_by = ?")
        params.append(str(filter_spec.requested_by))
    if not clauses:
        return "", []
    return " WHERE " + " AND ".join(clauses), params
