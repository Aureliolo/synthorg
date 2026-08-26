"""Postgres repository implementation for Plan."""

from typing import Final
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
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
from synthorg.persistence.postgres._integrity import raise_constraint_violation
from synthorg.persistence.postgres._plan_marshalling import (
    COLUMNS,
    INSERT_PLACEHOLDERS,
    UPDATE_SET,
    UPSERT_SET,
    row_params,
    row_to_plan,
    serialise_progress,
)

logger = get_logger(__name__)

_MAX_LIST_ROWS: Final[int] = 10_000


class PostgresPlanRepository:
    """Postgres-backed plan repository.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create(self, plan: Plan) -> None:
        """Insert a new plan, failing if the id already exists.

        Raises:
            DuplicateRecordError: A plan with this id already exists.
            ConstraintViolationError: An invariant the schema holds was
                broken, most often a ``parent_task_id`` naming no task.
                Typed rather than a retryable ``QueryError``: the insert
                is refused identically on every retry.
            QueryError: If the database operation fails.
        """
        msg = f"Failed to create plan {plan.id!r}"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    f"INSERT INTO plans ({COLUMNS}) "  # noqa: S608 -- fixed columns
                    f"VALUES {INSERT_PLACEHOLDERS}",
                    row_params(plan),
                )
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            logger.warning(
                PERSISTENCE_PLAN_SAVE_FAILED,
                plan_id=str(plan.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            duplicate = f"Plan with id {plan.id!r} already exists"
            raise DuplicateRecordError(duplicate) from exc
        except psycopg.errors.IntegrityError as exc:
            logger.warning(
                PERSISTENCE_PLAN_SAVE_FAILED,
                plan_id=str(plan.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                sqlstate=exc.sqlstate,
            )
            raise_constraint_violation(exc, msg)
        except psycopg.Error as exc:
            logger.warning(
                PERSISTENCE_PLAN_SAVE_FAILED,
                plan_id=str(plan.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

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
            guard = " AND version=%s"
            params.append(expected_version)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    # ``_UPDATE_SET`` + guard are fixed literals; values parameterized.
                    f"UPDATE plans SET {UPDATE_SET} WHERE id=%s{guard}",  # noqa: S608
                    params,
                )
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
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
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute("SELECT 1 FROM plans WHERE id = %s", (str(plan_id),))
                return await cur.fetchone() is not None
        except psycopg.Error as exc:
            msg = f"Failed to probe plan {plan_id!r}"
            logger.warning(
                PERSISTENCE_PLAN_FETCH_FAILED,
                plan_id=str(plan_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def save(self, plan: Plan) -> None:
        """Persist a plan via upsert.

        Raises:
            ConstraintViolationError: An invariant the schema holds was
                broken, most often a ``parent_task_id`` naming no task.
            QueryError: If the database query fails.
        """
        msg = f"Failed to save plan {plan.id!r}"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    # Fixed columns + derived SET; values fully parameterized.
                    f"INSERT INTO plans ({COLUMNS}) "  # noqa: S608
                    f"VALUES {INSERT_PLACEHOLDERS} "
                    f"ON CONFLICT(id) DO UPDATE SET {UPSERT_SET}",
                    row_params(plan),
                )
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            logger.warning(
                PERSISTENCE_PLAN_SAVE_FAILED,
                plan_id=str(plan.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                sqlstate=exc.sqlstate,
            )
            raise_constraint_violation(exc, msg)
        except psycopg.Error as exc:
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
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute("SELECT * FROM plans WHERE id = %s", (plan_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        except (ValueError, ValidationError, KeyError) as exc:
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

        Args:
            limit: Maximum plans to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Plans in ascending id order.
        """
        return await self.query(PlanFilterSpec(), limit=limit, offset=offset)

    @staticmethod
    def _conditions(filter_spec: PlanFilterSpec) -> tuple[list[str], list[object]]:
        """Build WHERE fragments + params for a filter spec.

        Returns:
            ``(conditions, params)`` where each condition is a hardcoded
            ``"<col> = %s"`` fragment and the values flow through ``params``.
        """
        conditions: list[str] = []
        params: list[object] = []
        if filter_spec.status is not None:
            conditions.append("status = %s")
            params.append(filter_spec.status.value)
        if filter_spec.project is not None:
            conditions.append("project = %s")
            params.append(filter_spec.project)
        if filter_spec.objective_id is not None:
            conditions.append("objective_id = %s")
            params.append(filter_spec.objective_id)
        if filter_spec.parent_task_id is not None:
            conditions.append("parent_task_id = %s")
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

        Args:
            filter_spec: Optional ``status`` / ``project`` /
                ``objective_id`` / ``parent_task_id`` filters.
            limit: Maximum plans to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Matching plans ordered by id.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PLAN_LIST_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        conditions, params = self._conditions(filter_spec)
        # Safety invariant: ``conditions`` only ever contains hardcoded
        # ``"<col> = %s"`` fragments; filter values stay parameterized in
        # ``params``. Never interpolate user-supplied text into ``conditions``.
        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = f"SELECT * FROM plans{where_clause} ORDER BY id LIMIT %s OFFSET %s"  # noqa: S608
        params.append(effective_limit)
        params.append(offset)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(query, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list plans"
            logger.warning(
                PERSISTENCE_PLAN_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            plans = tuple(row_to_plan(row) for row in rows)
        except (ValueError, ValidationError, KeyError) as exc:
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
        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = f"SELECT COUNT(*) FROM plans{where_clause}"  # noqa: S608
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(query, params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to count plans"
            logger.warning(
                PERSISTENCE_PLAN_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row["count"]) if row else 0

    async def delete(self, plan_id: NotBlankStr) -> bool:
        """Delete a plan by primary key.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute("DELETE FROM plans WHERE id = %s", (plan_id,))
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete plan {plan_id!r}"
            logger.warning(
                PERSISTENCE_PLAN_DELETE_FAILED,
                plan_id=plan_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted

    async def record_decomposition_progress(
        self,
        parent_task_id: NotBlankStr,
        /,
        *,
        progress: DecompositionProgress,
    ) -> Plan | None:
        """Stamp decomposition progress on the objective's ``PLANNING`` shell.

        Returns:
            The stamped shell, or ``None`` when none was there to take it.

        Raises:
            QueryError: If the database operation fails.
        """
        msg = f"Failed to record decomposition progress for {parent_task_id!r}"
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                # ONE row, chosen deterministically, and the same selection
                # the SQLite arm makes. Nothing constrains a parent task to a
                # single ``PLANNING`` plan, and a bare predicate would stamp
                # every match while ``RETURNING`` handed back an arbitrary
                # one, so the row announced need not be the row an operator
                # then reads.
                await cur.execute(
                    f"UPDATE plans SET decomposition_progress=%s "  # noqa: S608
                    f"WHERE id = (SELECT id FROM plans "
                    f"WHERE parent_task_id=%s AND status=%s ORDER BY id LIMIT 1) "
                    f"RETURNING {COLUMNS}",
                    (
                        serialise_progress(progress),
                        parent_task_id,
                        PlanStatus.PLANNING.value,
                    ),
                )
                row = await cur.fetchone()
                return row_to_plan(row) if row is not None else None
        except psycopg.Error as exc:
            logger.warning(
                PERSISTENCE_PLAN_SAVE_FAILED,
                parent_task_id=parent_task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

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
            QueryError: If the database query fails.
        """
        live_clause, live_params = live_task_predicate(terminal_statuses, "%s")
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                # One conditional DELETE, not a count followed by a delete.
                # Locking the plan row does not stop a task being inserted
                # against it, so a separate count leaves a window in which
                # work is filed under a plan the delete has already decided
                # to remove. Evaluating the guard inside the statement closes
                # it: the row goes only if no live task is visible to that
                # same statement.
                await cur.execute(
                    "DELETE FROM plans WHERE id = %s AND NOT EXISTS ("  # noqa: S608 -- clauses are fixed literals, values parameterized
                    f" SELECT 1 FROM tasks WHERE {live_clause})",
                    (plan_id, plan_id, *live_params),
                )
                if cur.rowcount > 0:
                    await conn.commit()
                    return PlanDeleteOutcome(deleted=True)
                # Counted in the transaction the refused DELETE ran in, so the
                # number reported is the one the guard refused on. A count
                # taken after the commit can see a task terminalise in between
                # and answer zero, and a refusal with zero live tasks reads as
                # a plan that was never there.
                await cur.execute(
                    f"SELECT COUNT(*) FROM tasks WHERE {live_clause}",  # noqa: S608 -- clauses are fixed literals, values parameterized
                    (plan_id, *live_params),
                )
                row = await cur.fetchone()
                live = int(row[0]) if row else 0
                await conn.commit()
                return PlanDeleteOutcome(deleted=False, live_task_count=live)
        except psycopg.Error as exc:
            msg = f"Failed to delete plan {plan_id!r}"
            logger.warning(
                PERSISTENCE_PLAN_DELETE_FAILED,
                plan_id=plan_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
