"""Postgres repository implementation for Plan."""

from typing import Final
from uuid import UUID

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    PersistenceVersionConflictError,
    QueryError,
    RecordNotFoundError,
)
from synthorg.core.plan import Plan, PlanItem, PlanVersionSnapshot
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.plan_review import PlanReview
from synthorg.core.task_enums import CoordinationTopology, TaskStructure
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
from synthorg.persistence._shared import coerce_row_timestamp
from synthorg.persistence._shared._task_filters import live_task_predicate
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.plan_protocol import PlanDeleteOutcome, PlanFilterSpec
from synthorg.persistence.postgres._integrity import raise_constraint_violation

logger = get_logger(__name__)

_MAX_LIST_ROWS: Final[int] = 10_000

_COLUMNS = (
    "id, project, objective_id, objective_title, parent_task_id, items, "
    "task_structure, coordination_topology, status, failure_reason, forecast_id, "
    "review, open_questions, assumptions, objective_criteria, version_history, "
    "replan_generation, version, created_at, updated_at, planning_strategy, "
    "review_absent_reason"
)
_COLUMN_NAMES = tuple(name.strip() for name in _COLUMNS.split(","))
# Derive placeholders + SET clauses from the single column list so the arity can
# never drift from ``_row_params`` (the sqlite repo drift-proofs the same way).
_INSERT_PLACEHOLDERS = "(" + ", ".join("%s" for _ in _COLUMN_NAMES) + ")"
_UPDATE_SET = ", ".join(f"{name}=%s" for name in _COLUMN_NAMES if name != "id")
_UPSERT_SET = ", ".join(
    f"{name}=EXCLUDED.{name}" for name in _COLUMN_NAMES if name != "id"
)


def _row_to_plan(row: DictRow) -> Plan:
    """Reconstruct a ``Plan`` from a Postgres dict_row.

    ``items`` arrives as a Python list of dicts (JSONB auto-deserialized by
    psycopg); ``created_at`` / ``updated_at`` arrive as aware ``datetime``
    values from their ``TIMESTAMPTZ`` columns. ``dict_row`` yields a fresh
    mutable ``dict`` per row, so the coercions rewrite it in place.

    Returns:
        Validated ``Plan`` model instance.
    """
    row["items"] = tuple(PlanItem.model_validate(item) for item in (row["items"] or []))
    row["task_structure"] = TaskStructure(row["task_structure"])
    row["coordination_topology"] = CoordinationTopology(row["coordination_topology"])
    row["status"] = PlanStatus(row["status"])
    forecast_id = row["forecast_id"]
    row["forecast_id"] = UUID(forecast_id) if forecast_id else None
    review = row["review"]
    row["review"] = PlanReview.model_validate(review) if review else None
    row["open_questions"] = tuple(row["open_questions"] or [])
    row["assumptions"] = tuple(row["assumptions"] or [])
    row["objective_criteria"] = tuple(row["objective_criteria"] or [])
    row["version_history"] = tuple(
        PlanVersionSnapshot.model_validate(snapshot)
        for snapshot in (row["version_history"] or [])
    )
    row["created_at"] = coerce_row_timestamp(row["created_at"])
    row["updated_at"] = coerce_row_timestamp(row["updated_at"])
    return Plan.model_validate(row)


class PostgresPlanRepository:
    """Postgres-backed plan repository.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @staticmethod
    def _row_params(plan: Plan) -> tuple[object, ...]:
        """Serialise a plan into positional SQL params in column order.

        Returns:
            Scalar param values for INSERT/UPDATE in the fixed column order.
        """
        return (
            str(plan.id),
            plan.project,
            plan.objective_id,
            plan.objective_title,
            plan.parent_task_id,
            Jsonb([item.model_dump(mode="json") for item in plan.items]),
            plan.task_structure.value,
            plan.coordination_topology.value,
            plan.status.value,
            plan.failure_reason,
            str(plan.forecast_id) if plan.forecast_id is not None else None,
            Jsonb(plan.review.model_dump(mode="json")) if plan.review else None,
            Jsonb(list(plan.open_questions)),
            Jsonb(list(plan.assumptions)),
            Jsonb(list(plan.objective_criteria)),
            Jsonb([snap.model_dump(mode="json") for snap in plan.version_history]),
            plan.replan_generation,
            plan.version,
            plan.created_at,
            plan.updated_at,
            plan.planning_strategy,
            plan.review_absent_reason,
        )

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
                    f"INSERT INTO plans ({_COLUMNS}) "  # noqa: S608 -- fixed columns
                    f"VALUES {_INSERT_PLACEHOLDERS}",
                    self._row_params(plan),
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
        params: list[object] = [*self._row_params(plan)[1:], str(plan.id)]
        guard = ""
        if expected_version is not None:
            guard = " AND version=%s"
            params.append(expected_version)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    # ``_UPDATE_SET`` + guard are fixed literals; values parameterized.
                    f"UPDATE plans SET {_UPDATE_SET} WHERE id=%s{guard}",  # noqa: S608
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
                    f"INSERT INTO plans ({_COLUMNS}) "  # noqa: S608
                    f"VALUES {_INSERT_PLACEHOLDERS} "
                    f"ON CONFLICT(id) DO UPDATE SET {_UPSERT_SET}",
                    self._row_params(plan),
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
            plan = _row_to_plan(row)
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
            plans = tuple(_row_to_plan(row) for row in rows)
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
                # The plan row is locked first so two deleters serialise and,
                # with a foreign key absent, so the count below is taken after
                # any writer holding the row has finished with it.
                await cur.execute(
                    "SELECT 1 FROM plans WHERE id = %s FOR UPDATE", (plan_id,)
                )
                if await cur.fetchone() is None:
                    await conn.commit()
                    return PlanDeleteOutcome(deleted=False)
                await cur.execute(
                    f"SELECT COUNT(*) FROM tasks WHERE {live_clause}",  # noqa: S608 -- clauses are fixed literals, values parameterized
                    (plan_id, *live_params),
                )
                row = await cur.fetchone()
                live = int(row[0]) if row else 0
                if live:
                    await conn.commit()
                    return PlanDeleteOutcome(deleted=False, live_task_count=live)
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
        return PlanDeleteOutcome(deleted=deleted)
