"""Postgres repository for tracked Docker container records."""

from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import DictRow, dict_row
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_TRACKED_CONTAINER_DELETE_FAILED,
    PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED,
    PERSISTENCE_TRACKED_CONTAINER_LOADED,
    PERSISTENCE_TRACKED_CONTAINER_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.tracked_container_protocol import TrackedContainerRecord

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class PostgresTrackedContainerRepository:
    """Postgres implementation of TrackedContainerRepository."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, record: TrackedContainerRecord) -> None:
        """Insert or replace the tracking row for one container.

        Raises:
            QueryError: If the database query fails.
        """
        params: tuple[object, ...] = (
            record.container_id,
            record.sidecar_id,
            normalize_utc(record.created_at),
        )
        sql = (
            "INSERT INTO tracked_containers "
            "(container_id, sidecar_id, created_at) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (container_id) DO UPDATE SET "
            "sidecar_id = EXCLUDED.sidecar_id, "
            "created_at = EXCLUDED.created_at"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save tracked container {record.container_id!r}"
            logger.warning(
                PERSISTENCE_TRACKED_CONTAINER_SAVE_FAILED,
                container_id=record.container_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, container_id: NotBlankStr) -> TrackedContainerRecord | None:
        """Read the tracking row for one container, or ``None`` if absent.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT container_id, sidecar_id, created_at "
                    "FROM tracked_containers WHERE container_id = %s",
                    (container_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to load tracked container {container_id!r}"
            logger.warning(
                PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED,
                container_id=container_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return self._row_to_record(row)

    async def delete(self, container_id: NotBlankStr) -> bool:
        """Delete the tracking row for one container.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM tracked_containers WHERE container_id = %s",
                    (container_id,),
                )
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete tracked container {container_id!r}"
            logger.warning(
                PERSISTENCE_TRACKED_CONTAINER_DELETE_FAILED,
                container_id=container_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0

    async def load_all(self) -> tuple[TrackedContainerRecord, ...]:
        """Load every tracking row (bespoke per ADR-0001 D7).

        Returns:
            Tuple of matching rows; empty when no rows match.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT container_id, sidecar_id, created_at "
                    "FROM tracked_containers"
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to load tracked container rows"
            logger.warning(
                PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        results = tuple(self._row_to_record(r) for r in rows)
        logger.debug(PERSISTENCE_TRACKED_CONTAINER_LOADED, count=len(results))
        return results

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[TrackedContainerRecord, ...]:
        """List tracked containers ordered by container_id ascending.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT container_id, sidecar_id, created_at "
                    "FROM tracked_containers "
                    "ORDER BY container_id ASC LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list tracked container rows"
            logger.warning(
                PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_record(r) for r in rows)

    def _row_to_record(self, row: DictRow) -> TrackedContainerRecord:
        """Row to record.

        Returns:
            Result of type ``TrackedContainerRecord``.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            row["created_at"] = normalize_utc(row["created_at"])
            return TrackedContainerRecord.model_validate(row)
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            msg = f"corrupt tracked_containers row {row.get('container_id')!r}"
            logger.warning(
                PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED,
                container_id=row.get("container_id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
