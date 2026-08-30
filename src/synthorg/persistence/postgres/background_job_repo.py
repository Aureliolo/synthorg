"""Postgres repository for backgrounded shell job records."""

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.background_job import (
    PERSISTENCE_BACKGROUND_JOB_DELETE_FAILED,
    PERSISTENCE_BACKGROUND_JOB_LOAD_FAILED,
    PERSISTENCE_BACKGROUND_JOB_LOADED,
    PERSISTENCE_BACKGROUND_JOB_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.background_job_protocol import (
    LIVE_BACKGROUND_JOB_STATUSES,
    BackgroundJobRecord,
    BackgroundJobStatus,
)

logger = get_logger(__name__)

_COLUMNS = (
    "job_id, container_id, owner_id, project_id, command_repr, pid, "
    "status, exit_code, output_path, started_at, updated_at, "
    "max_duration_seconds"
)
_COLUMN_COUNT = _COLUMNS.count(",") + 1
_LIVE_PLACEHOLDERS = ", ".join("%s" for _ in LIVE_BACKGROUND_JOB_STATUSES)


class PostgresBackgroundJobRepository:
    """Postgres implementation of BackgroundJobRepository."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: BackgroundJobRecord, /) -> None:
        """Insert or replace the tracking row for one job.

        Raises:
            QueryError: If the database query fails.
        """
        params: tuple[object, ...] = (
            entity.job_id,
            entity.container_id,
            entity.owner_id,
            entity.project_id,
            entity.command_repr,
            entity.pid,
            entity.status.value,
            entity.exit_code,
            entity.output_path,
            normalize_utc(entity.started_at),
            normalize_utc(entity.updated_at),
            entity.max_duration_seconds,
        )
        sql = (
            f"INSERT INTO background_jobs ({_COLUMNS}) "  # noqa: S608
            f"VALUES ({', '.join('%s' for _ in range(_COLUMN_COUNT))}) "
            "ON CONFLICT (job_id) DO UPDATE SET "
            "container_id = EXCLUDED.container_id, "
            "owner_id = EXCLUDED.owner_id, "
            "project_id = EXCLUDED.project_id, "
            "command_repr = EXCLUDED.command_repr, "
            "pid = EXCLUDED.pid, "
            "status = EXCLUDED.status, "
            "exit_code = EXCLUDED.exit_code, "
            "output_path = EXCLUDED.output_path, "
            "started_at = EXCLUDED.started_at, "
            "updated_at = EXCLUDED.updated_at, "
            "max_duration_seconds = EXCLUDED.max_duration_seconds"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save background job {entity.job_id!r}"
            logger.warning(
                PERSISTENCE_BACKGROUND_JOB_SAVE_FAILED,
                job_id=entity.job_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def save_if_live(self, entity: BackgroundJobRecord, /) -> bool:
        """Persist *entity* only if the existing row is still live.

        Returns:
            ``True`` if the write applied, ``False`` if the existing
            row had already moved to a terminal status.

        Raises:
            QueryError: If the database query fails.
        """
        params: tuple[object, ...] = (
            entity.container_id,
            entity.owner_id,
            entity.project_id,
            entity.command_repr,
            entity.pid,
            entity.status.value,
            entity.exit_code,
            entity.output_path,
            normalize_utc(entity.started_at),
            normalize_utc(entity.updated_at),
            entity.max_duration_seconds,
            entity.job_id,
            *(s.value for s in LIVE_BACKGROUND_JOB_STATUSES),
        )
        sql = (
            "UPDATE background_jobs SET "  # noqa: S608
            "container_id = %s, owner_id = %s, project_id = %s, "
            "command_repr = %s, pid = %s, status = %s, exit_code = %s, "
            "output_path = %s, started_at = %s, updated_at = %s, "
            "max_duration_seconds = %s "
            f"WHERE job_id = %s AND status IN ({_LIVE_PLACEHOLDERS})"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                applied = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to conditionally save background job {entity.job_id!r}"
            logger.warning(
                PERSISTENCE_BACKGROUND_JOB_SAVE_FAILED,
                job_id=entity.job_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return applied

    async def get(self, entity_id: NotBlankStr, /) -> BackgroundJobRecord | None:
        """Read the tracking row for one job, or ``None`` if absent.

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
                    f"SELECT {_COLUMNS} FROM background_jobs "  # noqa: S608
                    "WHERE job_id = %s",
                    (entity_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to load background job {entity_id!r}"
            logger.warning(
                PERSISTENCE_BACKGROUND_JOB_LOAD_FAILED,
                job_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return self._row_to_record(row)

    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete the tracking row for one job.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM background_jobs WHERE job_id = %s",
                    (entity_id,),
                )
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete background job {entity_id!r}"
            logger.warning(
                PERSISTENCE_BACKGROUND_JOB_DELETE_FAILED,
                job_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BackgroundJobRecord, ...]:
        """List jobs ordered by job_id ascending.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_BACKGROUND_JOB_LOAD_FAILED
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_COLUMNS} FROM background_jobs "  # noqa: S608
                    "ORDER BY job_id ASC LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list background job rows"
            logger.warning(
                PERSISTENCE_BACKGROUND_JOB_LOAD_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_record(r) for r in rows)

    async def load_all(self) -> tuple[BackgroundJobRecord, ...]:
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
                    f"SELECT {_COLUMNS} FROM background_jobs"  # noqa: S608
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to load background job rows"
            logger.warning(
                PERSISTENCE_BACKGROUND_JOB_LOAD_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        results = tuple(self._row_to_record(r) for r in rows)
        logger.debug(PERSISTENCE_BACKGROUND_JOB_LOADED, count=len(results))
        return results

    async def list_by_container(
        self,
        container_id: NotBlankStr,
        *,
        statuses: frozenset[BackgroundJobStatus] | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BackgroundJobRecord, ...]:
        """List jobs recorded against one container, newest-first.

        Returns:
            Job rows recorded against this container, newest-first.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_BACKGROUND_JOB_LOAD_FAILED
        )
        status_clause = ""
        params: tuple[object, ...] = (container_id,)
        if statuses is not None:
            placeholders = ", ".join("%s" for _ in statuses)
            status_clause = f" AND status IN ({placeholders})"
            params = (container_id, *(s.value for s in statuses))
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_COLUMNS} FROM background_jobs "  # noqa: S608
                    f"WHERE container_id = %s{status_clause} "
                    "ORDER BY started_at DESC LIMIT %s OFFSET %s",
                    (*params, limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Failed to list background jobs for container {container_id!r}"
            logger.warning(
                PERSISTENCE_BACKGROUND_JOB_LOAD_FAILED,
                container_id=container_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_record(r) for r in rows)

    async def count_live_by_owner(self, owner_id: NotBlankStr) -> int:
        """Count jobs in a live status for one lifecycle owner.

        Returns:
            The number of live-status rows for this owner.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM background_jobs "  # noqa: S608
                    f"WHERE owner_id = %s AND status IN ({_LIVE_PLACEHOLDERS})",
                    (owner_id, *(s.value for s in LIVE_BACKGROUND_JOB_STATUSES)),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to count live background jobs for owner {owner_id!r}"
            logger.warning(
                PERSISTENCE_BACKGROUND_JOB_LOAD_FAILED,
                owner_id=owner_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row[0]) if row is not None else 0

    async def list_by_owner(
        self,
        owner_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[BackgroundJobRecord, ...]:
        """List jobs recorded against one lifecycle owner, newest-first.

        Returns:
            Job rows recorded against this owner, newest-first.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_BACKGROUND_JOB_LOAD_FAILED
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    f"SELECT {_COLUMNS} FROM background_jobs "  # noqa: S608
                    "WHERE owner_id = %s "
                    "ORDER BY started_at DESC LIMIT %s OFFSET %s",
                    (owner_id, limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Failed to list background jobs for owner {owner_id!r}"
            logger.warning(
                PERSISTENCE_BACKGROUND_JOB_LOAD_FAILED,
                owner_id=owner_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_record(r) for r in rows)

    def _row_to_record(self, row: DictRow) -> BackgroundJobRecord:
        """Row to record.

        Returns:
            Result of type ``BackgroundJobRecord``.

        Raises:
            QueryError: If the row cannot be parsed.
        """
        try:
            row["status"] = BackgroundJobStatus(str(row["status"]))
            row["started_at"] = normalize_utc(row["started_at"])
            row["updated_at"] = normalize_utc(row["updated_at"])
            return BackgroundJobRecord.model_validate(row)
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            msg = f"corrupt background_jobs row {row.get('job_id')!r}"
            logger.warning(
                PERSISTENCE_BACKGROUND_JOB_LOAD_FAILED,
                job_id=row.get("job_id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
