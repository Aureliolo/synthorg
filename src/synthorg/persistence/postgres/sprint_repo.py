# module-kind: repository
"""Postgres repository for agile sprints.

Sibling of :class:`SQLiteSprintRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Satisfies ``SprintRepository``
structurally: id-keyed CRUD, atomic lifecycle transitions, and filtered
queries. Row <-> model marshalling is shared with the SQLite sibling via
:mod:`synthorg.persistence._shared.sprint_marshalling`.
"""

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.sprint import (
    PERSISTENCE_SPRINT_FAILED,
    PERSISTENCE_SPRINT_FETCHED,
    PERSISTENCE_SPRINT_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence._shared.sprint_marshalling import (
    SPRINT_COLUMNS,
    build_sprint_where,
    row_to_sprint,
    sprint_save_params,
    validate_sprint_update_keys,
)
from synthorg.persistence.sprint_protocol import SprintFilterSpec

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_ORDER_BY = "ORDER BY sprint_number DESC, id DESC"


def _encode_array_jsonb(values: tuple[str, ...]) -> object:
    """Wrap a sprint string-array column for binding to native JSONB.

    Returns:
        A :class:`~psycopg.types.json.Jsonb` adapter.
    """
    return Jsonb(list(values))


_UPSERT_SQL = f"""
    INSERT INTO sprints ({SPRINT_COLUMNS})
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        project = EXCLUDED.project,
        name = EXCLUDED.name,
        goal = EXCLUDED.goal,
        status = EXCLUDED.status,
        sprint_number = EXCLUDED.sprint_number,
        duration_days = EXCLUDED.duration_days,
        start_date = EXCLUDED.start_date,
        end_date = EXCLUDED.end_date,
        task_ids = EXCLUDED.task_ids,
        completed_task_ids = EXCLUDED.completed_task_ids,
        story_points_committed = EXCLUDED.story_points_committed,
        story_points_completed = EXCLUDED.story_points_completed
"""  # noqa: S608 -- column list is a compile-time constant

_TRANSITION_SQL = (
    "UPDATE sprints SET "
    "status = %s, "
    "start_date = COALESCE(%s, start_date), "
    "end_date = COALESCE(%s, end_date) "
    "WHERE id = %s AND status = %s"
)


class PostgresSprintRepository:
    """Postgres-backed agile sprint repository.

    Args:
        pool: Async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: Sprint) -> None:
        """Upsert a sprint row.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        params = sprint_save_params(entity, encode_array=_encode_array_jsonb)
        try:
            async with self._pool.connection() as conn:
                await conn.execute(_UPSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            msg = (
                f"Constraint violation saving sprint {entity.id!r}: "
                f"{safe_error_description(exc)}"
            )
            logger.warning(
                PERSISTENCE_SPRINT_FAILED,
                operation="save",
                sprint_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=str(exc)) from exc
        except psycopg.Error as exc:
            msg = (
                f"Failed to save sprint {entity.id!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            logger.warning(
                PERSISTENCE_SPRINT_FAILED,
                operation="save",
                sprint_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> Sprint | None:
        """Get a sprint by id, or ``None`` if not found.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {SPRINT_COLUMNS} FROM sprints WHERE id = %s"  # noqa: S608
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (entity_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch sprint {entity_id!r}"
            logger.warning(
                PERSISTENCE_SPRINT_FAILED,
                operation="get",
                sprint_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        sprint = row_to_sprint(row)
        logger.debug(PERSISTENCE_SPRINT_FETCHED, sprint_id=entity_id)
        return sprint

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Sprint, ...]:
        """List sprints newest-first.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_SPRINT_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        sql = (
            f"SELECT {SPRINT_COLUMNS} FROM sprints "  # noqa: S608
            f"{_ORDER_BY} LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (effective_limit, offset))
                rows = await cur.fetchall()
            items = tuple(row_to_sprint(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
            msg = "Failed to list sprints"
            logger.warning(
                PERSISTENCE_SPRINT_FAILED,
                operation="list_items",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_SPRINT_LISTED, count=len(items))
        return items

    async def query(
        self,
        filter_spec: SprintFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Sprint, ...]:
        """Return sprints matching the spec, newest-first (paginated).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_SPRINT_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        where, params = build_sprint_where(filter_spec, placeholder="%s")
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT {SPRINT_COLUMNS} FROM sprints
            WHERE {where}
            {_ORDER_BY}
            LIMIT %s OFFSET %s
        """  # noqa: S608 -- ``where`` is a closed set of column predicates
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
            items = tuple(row_to_sprint(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
            msg = "Failed to query sprints"
            logger.warning(
                PERSISTENCE_SPRINT_FAILED,
                operation="query",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_SPRINT_LISTED, count=len(items))
        return items

    async def count(self, filter_spec: SprintFilterSpec) -> int:
        """Count sprints matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = build_sprint_where(filter_spec, placeholder="%s")
        sql = f"SELECT COUNT(*) FROM sprints WHERE {where}"  # noqa: S608
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                assert row is not None  # noqa: S101 -- COUNT always returns a row
                return int(row[0])
        except psycopg.Error as exc:
            msg = "Failed to count sprints"
            logger.warning(
                PERSISTENCE_SPRINT_FAILED,
                operation="count",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: SprintStatus,
        to_state: SprintStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the sprint lifecycle state.

        Returns:
            ``True`` when the operation succeeded, ``False`` otherwise.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        validate_sprint_update_keys(updates)
        params = (
            to_state.value,
            updates.get("start_date"),
            updates.get("end_date"),
            entity_id,
            from_state.value,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_TRANSITION_SQL, params)
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            msg = (
                f"Constraint violation transitioning sprint {entity_id!r}: "
                f"{safe_error_description(exc)}"
            )
            logger.warning(
                PERSISTENCE_SPRINT_FAILED,
                operation="transition_if",
                sprint_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=str(exc)) from exc
        except psycopg.Error as exc:
            msg = f"Failed to transition sprint {entity_id!r}"
            logger.warning(
                PERSISTENCE_SPRINT_FAILED,
                operation="transition_if",
                sprint_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a sprint by id.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM sprints WHERE id = %s"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (entity_id,))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete sprint {entity_id!r}"
            logger.warning(
                PERSISTENCE_SPRINT_FAILED,
                operation="delete",
                sprint_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0


__all__ = ["PostgresSprintRepository"]
