"""Postgres repository for in-flight hiring requests.

Mirrors the SQLite implementation; the nested ``HiringRequest`` is
stored in a JSONB ``payload`` column (vs JSON text on SQLite).
"""

import json

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
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
from synthorg.persistence._shared import normalize_utc, validate_pagination_args
from synthorg.persistence.hiring_request_protocol import (
    HiringRequestFilterSpec,
)

logger = get_logger(__name__)

_UPSERT_SQL = """
INSERT INTO hiring_requests (
    id, status, requested_by, department, role, created_at, payload
)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    status = EXCLUDED.status,
    requested_by = EXCLUDED.requested_by,
    department = EXCLUDED.department,
    role = EXCLUDED.role,
    created_at = EXCLUDED.created_at,
    payload = EXCLUDED.payload
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
        normalize_utc(request.created_at),
        Jsonb(request.model_dump(mode="json")),
    )


def _row_to_request(payload: object) -> HiringRequest:
    """Deserialise a JSONB ``payload`` column into a ``HiringRequest``.

    Returns:
        The reconstructed ``HiringRequest``.

    Raises:
        QueryError: If the payload is not a JSON object or fails validation.
    """
    data = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(data, dict):
        msg = f"hiring_requests.payload is not a JSON object: {data!r}"
        raise QueryError(msg)
    try:
        return HiringRequest.model_validate(data)
    except ValidationError as exc:
        msg = f"corrupt hiring_requests payload: {data!r}"
        raise QueryError(msg) from exc


class PostgresHiringRequestRepository:
    """Postgres CRUD + status-filtered query for hiring requests.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: HiringRequest, /) -> None:
        """Upsert one hiring request keyed on its id.

        Raises:
            QueryError: If the write fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_UPSERT_SQL, _to_params(entity))
                await conn.commit()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"{_SELECT_SQL} WHERE id = %s",
                    (str(entity_id),),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            raise self._read_error(exc) from exc
        if row is None:
            return None
        return _row_to_request(row["payload"])

    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete the hiring request with ``entity_id``.

        Returns:
            ``True`` iff a row existed.

        Raises:
            QueryError: If the delete fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM hiring_requests WHERE id = %s",
                    (str(entity_id),),
                )
                removed = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, tuple(params))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            raise self._read_error(exc) from exc
        return int(row["cnt"]) if row else 0

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
            _SELECT_SQL
            + clause
            + " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
        )
        all_params = [*params, limit, offset]
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, tuple(all_params))
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            raise self._read_error(exc) from exc
        try:
            requests = tuple(_row_to_request(r["payload"]) for r in rows)
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

    def _read_error(self, exc: Exception) -> QueryError:
        msg = "Failed to query hiring requests"
        logger.warning(
            PERSISTENCE_HIRING_REQUEST_QUERY_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return QueryError(msg)


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
        clauses.append("status = %s")
        params.append(filter_spec.status.value)
    if filter_spec.requested_by is not None:
        clauses.append("requested_by = %s")
        params.append(str(filter_spec.requested_by))
    if not clauses:
        return "", []
    return " WHERE " + " AND ".join(clauses), params
