# module-kind: repository
"""SQLite repository for project charters.

Satisfies ``CharterRepository`` structurally: id-keyed CRUD, atomic
lifecycle transitions (``drafted -> approved | cancelled``), and
filtered queries by status / project / creator / conversation.

Tuple-valued charter fields (goals, constraints, success criteria, and
the in/out scope lists) are stored as JSON arrays; the budget envelope
is flattened into dedicated columns. Row <-> model marshalling is shared
with the Postgres sibling via
:mod:`synthorg.persistence._shared.charter_marshalling`.
"""

import sqlite3

import aiosqlite

from synthorg.core.enums import CharterStatus
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.models import ProjectCharter
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.persistence import (
    PERSISTENCE_CHARTER_FAILED,
    PERSISTENCE_CHARTER_FETCHED,
    PERSISTENCE_CHARTER_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence._shared.charter_marshalling import (
    CHARTER_COLUMNS,
    as_iso,
    build_charter_where,
    charter_save_params,
    row_to_charter,
    validate_charter_update_keys,
)
from synthorg.persistence.charter_protocol import CharterFilterSpec
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_UPSERT_SQL = f"""
    INSERT INTO project_charters ({CHARTER_COLUMNS})
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        conversation_id = excluded.conversation_id,
        created_by = excluded.created_by,
        version = excluded.version,
        status = excluded.status,
        title = excluded.title,
        brief = excluded.brief,
        goals = excluded.goals,
        constraints = excluded.constraints,
        success_criteria = excluded.success_criteria,
        in_scope = excluded.in_scope,
        out_of_scope = excluded.out_of_scope,
        envelope_amount = excluded.envelope_amount,
        envelope_currency = excluded.envelope_currency,
        envelope_deadline = excluded.envelope_deadline,
        envelope_time_horizon = excluded.envelope_time_horizon,
        project_id = excluded.project_id,
        proposed_project_name = excluded.proposed_project_name,
        proposed_project_description = excluded.proposed_project_description,
        updated_at = excluded.updated_at,
        approved_at = excluded.approved_at,
        approved_by = excluded.approved_by,
        forecast_id = excluded.forecast_id,
        correlation_id = excluded.correlation_id,
        task_id = excluded.task_id
"""  # noqa: S608 -- column list is a compile-time constant


async def _safe_rollback(
    db: aiosqlite.Connection,
    *,
    operation: str,
    **log_context: object,
) -> None:
    """Roll back the current transaction, logging any rollback failure."""
    try:
        await db.rollback()
    except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
        log_exception_redacted(
            logger,
            PERSISTENCE_CHARTER_FAILED,
            rollback_exc,
            phase="rollback",
            operation=operation,
            **log_context,
        )


class SQLiteCharterRepository:
    """SQLite-backed project charter repository.

    Args:
        db: An open aiosqlite connection.
        write_context: Async write-serialising context manager.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context

    async def save(self, entity: ProjectCharter) -> None:
        """Upsert a charter row.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        params = charter_save_params(entity)
        async with self._write_context():
            try:
                await self._db.execute(_UPSERT_SQL, params)
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await _safe_rollback(self._db, operation="save", charter_id=entity.id)
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
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(self._db, operation="save", charter_id=entity.id)
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
        sql = f"SELECT {CHARTER_COLUMNS} FROM project_charters WHERE id = ?"  # noqa: S608
        try:
            cursor = await self._db.execute(sql, (entity_id,))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        """List charters newest-first (``created_at DESC, id DESC``).

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
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        )
        try:
            cursor = await self._db.execute(sql, (effective_limit, offset))
            rows = await cursor.fetchall()
            items = tuple(row_to_charter(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        where, params = build_charter_where(filter_spec, placeholder="?")
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT {CHARTER_COLUMNS} FROM project_charters
            WHERE {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
        """  # noqa: S608 -- ``where`` is a closed set of column predicates
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
            items = tuple(row_to_charter(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        where, params = build_charter_where(filter_spec, placeholder="?")
        sql = (
            "SELECT COUNT(*) FROM project_charters "  # noqa: S608
            f"WHERE {where}"
        )
        try:
            cursor = await self._db.execute(sql, params)
            row = await cursor.fetchone()
            assert row is not None  # noqa: S101 -- COUNT always returns a row
            return int(row[0])
        except (sqlite3.Error, aiosqlite.Error) as exc:
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

        ``**updates`` carries the columns stamped at the transition
        (``updated_at`` plus the approval provenance on approval). Each
        is applied via ``COALESCE`` so a missing key leaves the column
        unchanged (a drafted row's approval columns are NULL, so a
        cancel keeps them NULL while an approve sets them).

        Returns:
            ``True`` when the operation succeeded, ``False`` otherwise.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        validate_charter_update_keys(updates)
        sql = (
            "UPDATE project_charters SET "
            "status = ?, "
            "updated_at = COALESCE(?, updated_at), "
            "approved_at = COALESCE(?, approved_at), "
            "approved_by = COALESCE(?, approved_by), "
            "forecast_id = COALESCE(?, forecast_id), "
            "correlation_id = COALESCE(?, correlation_id), "
            "task_id = COALESCE(?, task_id) "
            "WHERE id = ? AND status = ?"
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
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, params)
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await _safe_rollback(
                    self._db, operation="transition_if", charter_id=entity_id
                )
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
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db, operation="transition_if", charter_id=entity_id
                )
                msg = f"Failed to transition charter {entity_id!r}"
                logger.warning(
                    PERSISTENCE_CHARTER_FAILED,
                    operation="transition_if",
                    charter_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a charter by id.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM project_charters WHERE id = ?"
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, (entity_id,))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(self._db, operation="delete", charter_id=entity_id)
                msg = f"Failed to delete charter {entity_id!r}"
                logger.warning(
                    PERSISTENCE_CHARTER_FAILED,
                    operation="delete",
                    charter_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0


__all__ = ["SQLiteCharterRepository"]
