# module-kind: repository
"""Postgres repository for project charters.

Sibling of :class:`SQLiteCharterRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Satisfies ``CharterRepository``
structurally: id-keyed CRUD, atomic lifecycle transitions
(``drafted -> approved | cancelled``), and filtered queries. Row <-> model
marshalling is shared with the SQLite sibling via
:mod:`synthorg.persistence._shared.charter_marshalling`.
"""

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.enums import CharterStatus
from synthorg.meta.charter.models import ProjectCharter
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.charter import (
    PERSISTENCE_CHARTER_FAILED,
    PERSISTENCE_CHARTER_FETCHED,
    PERSISTENCE_CHARTER_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence._shared.charter_marshalling import (
    CHARTER_CAS_UPDATE_SQL_PCT,
    CHARTER_COLUMNS,
    as_iso,
    build_charter_where,
    charter_cas_params,
    charter_save_params,
    row_to_charter,
    validate_charter_update_keys,
)
from synthorg.persistence.charter_protocol import CharterFilterSpec

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_UPSERT_SQL = f"""
    INSERT INTO project_charters ({CHARTER_COLUMNS})
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO UPDATE SET
        conversation_id = EXCLUDED.conversation_id,
        created_by = EXCLUDED.created_by,
        version = EXCLUDED.version,
        status = EXCLUDED.status,
        title = EXCLUDED.title,
        brief = EXCLUDED.brief,
        goals = EXCLUDED.goals,
        constraints = EXCLUDED.constraints,
        success_criteria = EXCLUDED.success_criteria,
        in_scope = EXCLUDED.in_scope,
        out_of_scope = EXCLUDED.out_of_scope,
        envelope_amount = EXCLUDED.envelope_amount,
        envelope_currency = EXCLUDED.envelope_currency,
        envelope_deadline = EXCLUDED.envelope_deadline,
        envelope_time_horizon = EXCLUDED.envelope_time_horizon,
        project_id = EXCLUDED.project_id,
        proposed_project_name = EXCLUDED.proposed_project_name,
        proposed_project_description = EXCLUDED.proposed_project_description,
        updated_at = EXCLUDED.updated_at,
        approved_at = EXCLUDED.approved_at,
        approved_by = EXCLUDED.approved_by,
        forecast_id = EXCLUDED.forecast_id,
        correlation_id = EXCLUDED.correlation_id,
        task_id = EXCLUDED.task_id
"""  # noqa: S608 -- column list is a compile-time constant


class PostgresCharterRepository:
    """Postgres-backed project charter repository.

    Args:
        pool: Async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: ProjectCharter) -> None:
        """Upsert a charter row.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        params = charter_save_params(entity)
        try:
            async with self._pool.connection() as conn:
                await conn.execute(_UPSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            msg = (
                f"Constraint violation saving charter {entity.id!r}: "
                f"{safe_error_description(exc)}"
            )
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="save",
                charter_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=str(exc)) from exc
        except psycopg.Error as exc:
            msg = (
                f"Failed to save charter {entity.id!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="save",
                charter_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> ProjectCharter | None:
        """Get a charter by id, or ``None`` if not found.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {CHARTER_COLUMNS} FROM project_charters WHERE id = %s"  # noqa: S608
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (entity_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch charter {entity_id!r}"
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="get",
                charter_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        charter = row_to_charter(row)
        logger.debug(PERSISTENCE_CHARTER_FETCHED, charter_id=entity_id)
        return charter

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ProjectCharter, ...]:
        """List charters newest-first.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CHARTER_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        sql = (
            f"SELECT {CHARTER_COLUMNS} FROM project_charters "  # noqa: S608
            "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (effective_limit, offset))
                rows = await cur.fetchall()
            items = tuple(row_to_charter(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
            msg = "Failed to list charters"
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="list_items",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_CHARTER_LISTED, count=len(items))
        return items

    async def query(
        self,
        filter_spec: CharterFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ProjectCharter, ...]:
        """Return charters matching the spec, newest-first (paginated).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CHARTER_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        where, params = build_charter_where(filter_spec, placeholder="%s")
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT {CHARTER_COLUMNS} FROM project_charters
            WHERE {where}
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
        """  # noqa: S608 -- ``where`` is a closed set of column predicates
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
            items = tuple(row_to_charter(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
            msg = "Failed to query charters"
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="query",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_CHARTER_LISTED, count=len(items))
        return items

    async def count(self, filter_spec: CharterFilterSpec) -> int:
        """Count charters matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = build_charter_where(filter_spec, placeholder="%s")
        sql = (
            "SELECT COUNT(*) FROM project_charters "  # noqa: S608
            f"WHERE {where}"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                assert row is not None  # noqa: S101 -- COUNT always returns a row
                return int(row[0])
        except psycopg.Error as exc:
            msg = "Failed to count charters"
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="count",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: CharterStatus,
        to_state: CharterStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the charter lifecycle state.

        Returns:
            ``True`` when the operation succeeded, ``False`` otherwise.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        validate_charter_update_keys(updates)
        sql = (
            "UPDATE project_charters SET "
            "status = %s, "
            "updated_at = COALESCE(%s, updated_at), "
            "approved_at = COALESCE(%s, approved_at), "
            "approved_by = COALESCE(%s, approved_by), "
            "forecast_id = COALESCE(%s, forecast_id), "
            "correlation_id = COALESCE(%s, correlation_id), "
            "task_id = COALESCE(%s, task_id) "
            "WHERE id = %s AND status = %s"
        )
        forecast_update = updates.get("forecast_id")
        params = (
            to_state.value,
            as_iso(updates.get("updated_at")),
            as_iso(updates.get("approved_at")),
            updates.get("approved_by"),
            (str(forecast_update) if forecast_update is not None else None),
            updates.get("correlation_id"),
            updates.get("task_id"),
            entity_id,
            from_state.value,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            msg = (
                f"Constraint violation transitioning charter {entity_id!r}: "
                f"{safe_error_description(exc)}"
            )
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="transition_if",
                charter_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=str(exc)) from exc
        except psycopg.Error as exc:
            msg = f"Failed to transition charter {entity_id!r}"
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="transition_if",
                charter_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0

    async def save_edit_if_version(
        self,
        entity: ProjectCharter,
        *,
        expected_version: int,
    ) -> bool:
        """Conditionally persist an edited charter (optimistic concurrency).

        Applies the full ``entity`` only when the stored row is still at
        ``expected_version`` AND ``DRAFTED``, so a concurrent edit or
        approve / cancel cannot be silently clobbered (ADR-0001 D7
        lost-update invariant).

        Returns:
            ``True`` when one row was updated; ``False`` on a version /
            status mismatch (or missing row).

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        params = charter_cas_params(entity, expected_version=expected_version)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(CHARTER_CAS_UPDATE_SQL_PCT, params)
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            msg = (
                f"Constraint violation editing charter {entity.id!r}: "
                f"{safe_error_description(exc)}"
            )
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="save_edit_if_version",
                charter_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConstraintViolationError(msg, constraint=str(exc)) from exc
        except psycopg.Error as exc:
            msg = f"Failed to edit charter {entity.id!r}"
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="save_edit_if_version",
                charter_id=entity.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a charter by id.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM project_charters WHERE id = %s"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (entity_id,))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete charter {entity_id!r}"
            logger.warning(
                PERSISTENCE_CHARTER_FAILED,
                operation="delete",
                charter_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0


__all__ = ["PostgresCharterRepository"]
