"""SQLite repository implementation for Plan."""

import json
import sqlite3
from typing import Final
from uuid import UUID

import aiosqlite
from pydantic import ValidationError

from synthorg.core.decomposition_progress import DecompositionProgress
from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    PersistenceVersionConflictError,
    QueryError,
    RecordNotFoundError,
)
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.plan import (
    PERSISTENCE_PLAN_DELETE_FAILED,
    PERSISTENCE_PLAN_DESERIALIZE_FAILED,
    PERSISTENCE_PLAN_FETCH_FAILED,
    PERSISTENCE_PLAN_FETCHED,
    PERSISTENCE_PLAN_LIST_FAILED,
    PERSISTENCE_PLAN_LISTED,
    PERSISTENCE_PLAN_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared._task_filters import live_task_predicate
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.plan_protocol import PlanDeleteOutcome, PlanFilterSpec
from synthorg.persistence.sqlite._integrity import raise_constraint_violation
from synthorg.persistence.sqlite._plan_marshalling import (
    COLUMNS,
    INSERT_PLACEHOLDERS,
    UPDATE_SET,
    UPSERT_SET,
    row_params,
    row_to_plan,
    serialise_progress,
)
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)

logger = get_logger(__name__)

_MAX_LIST_ROWS: Final[int] = 10_000


class SQLitePlanRepository:
    """SQLite-backed plan repository.

    Args:
        db: An open aiosqlite connection with ``row_factory`` set to
            ``aiosqlite.Row``.
        write_context: Async context manager that serializes writes on the
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

    async def create(self, plan: Plan) -> None:
        """Insert a new plan, failing if the id already exists.

        Raises:
            DuplicateRecordError: A plan with the same id exists.
            ConstraintViolationError: An invariant the schema holds was
                broken, most often a ``parent_task_id`` naming no task.
                Typed rather than a retryable ``QueryError``: the insert
                is refused identically on every retry.
            QueryError: If the database operation fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    f"INSERT INTO plans ({COLUMNS}) "  # noqa: S608
                    f"VALUES {INSERT_PLACEHOLDERS}",
                    row_params(plan),
                )
                await self._db.commit()
            except (sqlite3.IntegrityError, aiosqlite.IntegrityError) as exc:
                await self._safe_rollback()
                logger.warning(
                    PERSISTENCE_PLAN_SAVE_FAILED,
                    plan_id=str(plan.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    sqlite_errorname=getattr(exc, "sqlite_errorname", None),
                )
                if is_unique_constraint_error(exc):
                    msg = f"Plan with id {plan.id!r} already exists"
                    raise DuplicateRecordError(msg) from exc
                msg = f"Failed to create plan {plan.id!r}"
                raise_constraint_violation(exc, msg)
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = f"Failed to create plan {plan.id!r}"
                logger.warning(
                    PERSISTENCE_PLAN_SAVE_FAILED,
                    plan_id=str(plan.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def _safe_rollback(self) -> None:
        """Best-effort rollback on the shared connection.

        A secondary rollback failure is logged, not raised, so it never masks
        the original error the caller is propagating.
        """
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                PERSISTENCE_PLAN_SAVE_FAILED,
                error_type=type(rollback_exc).__name__,
                error=safe_error_description(rollback_exc),
                rollback_failed=True,
            )

    async def update(self, plan: Plan, *, expected_version: int | None = None) -> None:
        """Update an existing plan, failing if no row matched.

        Raises:
            PersistenceVersionConflictError: ``expected_version`` was supplied
                and the stored version has moved (a concurrent write won).
            RecordNotFoundError: No plan with this id exists.
            QueryError: If the database operation fails.
        """
        params: list[object] = [*row_params(plan)[1:], str(plan.id)]
        guard = ""
        if expected_version is not None:
            guard = " AND version=?"
            params.append(expected_version)
        async with self._write_context():
            try:
                async with self._db.execute(
                    f"UPDATE plans SET {UPDATE_SET} WHERE id=?{guard}",  # noqa: S608 -- clauses are fixed literals, values parameterized
                    params,
                ) as cursor:
                    await self._db.commit()
                    rowcount = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = f"Failed to update plan {plan.id!r}"
                logger.warning(
                    PERSISTENCE_PLAN_SAVE_FAILED,
                    plan_id=str(plan.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            if rowcount == 0:
                await self._raise_update_miss(plan, expected_version)

    async def _raise_update_miss(
        self, plan: Plan, expected_version: int | None
    ) -> None:
        """Classify a zero-rowcount update as a version conflict or a miss.

        Raises:
            PersistenceVersionConflictError: The row exists but its version
                moved (only distinguishable when ``expected_version`` is set).
            RecordNotFoundError: No plan with this id exists at all.
        """
        if expected_version is not None and await self._row_exists(plan.id):
            logger.warning(
                PERSISTENCE_PLAN_SAVE_FAILED,
                plan_id=str(plan.id),
                error_type="PersistenceVersionConflictError",
                reason="version_conflict",
            )
            msg = f"Plan {plan.id!r} was modified concurrently"
            raise PersistenceVersionConflictError(msg)
        logger.warning(
            PERSISTENCE_PLAN_SAVE_FAILED,
            plan_id=str(plan.id),
            error_type="RecordNotFoundError",
            error="No plan with matching id",
        )
        msg = f"No plan with id {plan.id!r}"
        raise RecordNotFoundError(msg)

    async def _row_exists(self, plan_id: UUID) -> bool:
        """Return whether a plan row exists (id lookup only).

        Raises:
            QueryError: If the existence probe fails.
        """
        try:
            async with self._db.execute(
                "SELECT 1 FROM plans WHERE id = ?", (str(plan_id),)
            ) as cursor:
                return await cursor.fetchone() is not None
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to probe plan {plan_id!r}"
            raise QueryError(msg) from exc

    async def save(self, plan: Plan) -> None:
        """Persist a plan via upsert (migration / import paths).

        Raises:
            ConstraintViolationError: An invariant the schema holds was
                broken, most often a ``parent_task_id`` naming no task.
            QueryError: If the database operation fails.
        """
        async with self._write_context():
            msg = f"Failed to save plan {plan.id!r}"
            try:
                await self._db.execute(
                    f"INSERT INTO plans ({COLUMNS}) "  # noqa: S608 -- clauses are fixed literals, values parameterized
                    f"VALUES {INSERT_PLACEHOLDERS} "
                    f"ON CONFLICT(id) DO UPDATE SET {UPSERT_SET}",
                    row_params(plan),
                )
                await self._db.commit()
            except (sqlite3.IntegrityError, aiosqlite.IntegrityError) as exc:
                await self._safe_rollback()
                logger.warning(
                    PERSISTENCE_PLAN_SAVE_FAILED,
                    plan_id=str(plan.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    sqlite_errorname=getattr(exc, "sqlite_errorname", None),
                )
                raise_constraint_violation(exc, msg)
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                logger.warning(
                    PERSISTENCE_PLAN_SAVE_FAILED,
                    plan_id=str(plan.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, plan_id: NotBlankStr) -> Plan | None:
        """Retrieve a plan by primary key.

        Returns:
            The matching ``Plan``, or ``None`` if not found.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        try:
            async with self._db.execute(
                "SELECT * FROM plans WHERE id = ?", (plan_id,)
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch plan {plan_id!r}"
            logger.warning(
                PERSISTENCE_PLAN_FETCH_FAILED,
                plan_id=plan_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(PERSISTENCE_PLAN_FETCHED, plan_id=plan_id, found=False)
            return None
        try:
            plan = row_to_plan(row)
        except (ValueError, ValidationError, json.JSONDecodeError, KeyError) as exc:
            msg = f"Failed to deserialize plan {plan_id!r}"
            logger.warning(
                PERSISTENCE_PLAN_DESERIALIZE_FAILED,
                plan_id=plan_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_PLAN_FETCHED, plan_id=plan_id, found=True)
        return plan

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Plan, ...]:
        """List all plans in id order.

        Returns:
            Plans in ascending id order.

        Raises:
            QueryError: If the query, deserialization, or pagination fails.
        """
        return await self.query(PlanFilterSpec(), limit=limit, offset=offset)

    def _conditions(
        self, filter_spec: PlanFilterSpec
    ) -> tuple[list[str], list[object]]:
        """Build WHERE fragments + params for a filter spec.

        Returns:
            ``(conditions, params)`` where each condition is a hardcoded
            ``"<col> = ?"`` fragment and the values flow through ``params``.
        """
        conditions: list[str] = []
        params: list[object] = []
        if filter_spec.status is not None:
            conditions.append("status = ?")
            params.append(filter_spec.status.value)
        if filter_spec.project is not None:
            conditions.append("project = ?")
            params.append(filter_spec.project)
        if filter_spec.objective_id is not None:
            conditions.append("objective_id = ?")
            params.append(filter_spec.objective_id)
        if filter_spec.parent_task_id is not None:
            conditions.append("parent_task_id = ?")
            params.append(filter_spec.parent_task_id)
        return conditions, params

    async def query(
        self,
        filter_spec: PlanFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Plan, ...]:
        """List plans matching the filter spec, ordered by id ascending.

        Returns:
            Matching plans ordered by id.

        Raises:
            QueryError: If the query, deserialization, or pagination fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PLAN_LIST_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        conditions, params = self._conditions(filter_spec)
        query = "SELECT * FROM plans"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id LIMIT ? OFFSET ?"
        params.append(effective_limit)
        params.append(offset)
        try:
            async with self._db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list plans"
            logger.warning(
                PERSISTENCE_PLAN_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            plans = tuple(row_to_plan(row) for row in rows)
        except (ValueError, ValidationError, json.JSONDecodeError, KeyError) as exc:
            msg = "Failed to deserialize plans"
            logger.warning(
                PERSISTENCE_PLAN_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_PLAN_LISTED, count=len(plans))
        return plans

    async def count(self, filter_spec: PlanFilterSpec) -> int:
        """Count plans matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        conditions, params = self._conditions(filter_spec)
        query = "SELECT COUNT(*) FROM plans"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        try:
            async with self._db.execute(query, params) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to count plans"
            logger.warning(
                PERSISTENCE_PLAN_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row[0]) if row else 0

    async def delete(self, plan_id: NotBlankStr) -> bool:
        """Delete a plan by primary key.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM plans WHERE id = ?", (plan_id,)
                ) as cursor:
                    await self._db.commit()
                    rowcount = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = f"Failed to delete plan {plan_id!r}"
                logger.warning(
                    PERSISTENCE_PLAN_DELETE_FAILED,
                    plan_id=plan_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return rowcount > 0

    async def record_decomposition_progress(
        self,
        parent_task_id: NotBlankStr,
        /,
        *,
        progress: DecompositionProgress,
    ) -> bool:
        """Stamp decomposition progress on the objective's ``PLANNING`` shell.

        Returns:
            ``True`` when a shell took the stamp.

        Raises:
            QueryError: If the operation fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "UPDATE plans SET decomposition_progress=? "
                    "WHERE parent_task_id=? AND status=?",
                    (
                        serialise_progress(progress),
                        parent_task_id,
                        PlanStatus.PLANNING.value,
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = f"Failed to record decomposition progress for {parent_task_id!r}"
                logger.warning(
                    PERSISTENCE_PLAN_SAVE_FAILED,
                    parent_task_id=parent_task_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return cursor.rowcount > 0

    async def delete_if_no_live_tasks(
        self,
        plan_id: NotBlankStr,
        *,
        terminal_statuses: frozenset[str],
    ) -> PlanDeleteOutcome:
        """Delete a plan only while nothing is still building under it.

        Returns:
            The outcome of the guarded delete.

        Raises:
            QueryError: If the database operation fails.
        """
        live_clause, live_params = live_task_predicate(terminal_statuses, "?")
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM plans WHERE id = ? AND NOT EXISTS ("  # noqa: S608 -- clauses are fixed literals, values parameterized
                    f" SELECT 1 FROM tasks WHERE {live_clause})",
                    (plan_id, plan_id, *live_params),
                ) as cursor:
                    rowcount = cursor.rowcount
                if rowcount > 0:
                    await self._db.commit()
                    return PlanDeleteOutcome(deleted=True)
                # Counted inside the transaction the refused DELETE ran in, so
                # the number reported is the one the guard actually refused
                # on. Counting after the commit lets a task terminalise in
                # between and answer zero, and a refusal with zero live tasks
                # is indistinguishable from a plan that was never there.
                async with self._db.execute(
                    f"SELECT COUNT(*) FROM tasks WHERE {live_clause}",  # noqa: S608 -- clauses are fixed literals, values parameterized
                    (plan_id, *live_params),
                ) as cursor:
                    row = await cursor.fetchone()
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = f"Failed to delete plan {plan_id!r}"
                logger.warning(
                    PERSISTENCE_PLAN_DELETE_FAILED,
                    plan_id=plan_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return PlanDeleteOutcome(
                deleted=False, live_task_count=int(row[0]) if row else 0
            )
