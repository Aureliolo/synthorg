"""Postgres repository implementation for Plan."""

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
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
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
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.plan_protocol import PlanFilterSpec

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


def _row_to_plan(row: DictRow) -> Plan:
    """Reconstruct a ``Plan`` from a Postgres dict_row.

    ``items`` arrives as a Python list of dicts (JSONB auto-deserialized by
    psycopg); ``created_at`` / ``updated_at`` arrive as aware ``datetime``
    values from their ``TIMESTAMPTZ`` columns.

    Returns:
        Validated ``Plan`` model instance.
    """
    data = dict(row)
    data["items"] = tuple(
        PlanItem.model_validate(item) for item in (data["items"] or [])
    )
    data["task_structure"] = TaskStructure(data["task_structure"])
    data["coordination_topology"] = CoordinationTopology(data["coordination_topology"])
    data["status"] = PlanStatus(data["status"])
    forecast_id = data["forecast_id"]
    data["forecast_id"] = UUID(forecast_id) if forecast_id else None
    data["created_at"] = coerce_row_timestamp(data["created_at"])
    data["updated_at"] = coerce_row_timestamp(data["updated_at"])
    return Plan.model_validate(data)


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
            plan.parent_task_id,
            Jsonb([item.model_dump(mode="json") for item in plan.items]),
            plan.task_structure.value,
            plan.coordination_topology.value,
            plan.status.value,
            str(plan.forecast_id) if plan.forecast_id is not None else None,
            plan.version,
            plan.created_at,
            plan.updated_at,
        )

    async def create(self, plan: Plan) -> None:
        """Insert a new plan, failing if the id already exists.

        Raises:
            DuplicateRecordError: A plan with this id already exists.
            QueryError: If the database operation fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO plans (id, project, objective_id, parent_task_id,
                                       items, task_structure, coordination_topology,
                                       status, forecast_id, version, created_at,
                                       updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
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
            msg = f"Plan with id {plan.id!r} already exists"
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to create plan {plan.id!r}"
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
                    f"""
                    UPDATE plans SET
                        project=%s,
                        objective_id=%s,
                        parent_task_id=%s,
                        items=%s,
                        task_structure=%s,
                        coordination_topology=%s,
                        status=%s,
                        forecast_id=%s,
                        version=%s,
                        created_at=%s,
                        updated_at=%s
                    WHERE id=%s{guard}
                    """,  # noqa: S608 -- guard is a fixed literal, values parameterized
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
            raise QueryError(msg) from exc

    async def save(self, plan: Plan) -> None:
        """Persist a plan via upsert.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO plans (id, project, objective_id, parent_task_id,
                                       items, task_structure, coordination_topology,
                                       status, forecast_id, version, created_at,
                                       updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(id) DO UPDATE SET
                        project=EXCLUDED.project,
                        objective_id=EXCLUDED.objective_id,
                        parent_task_id=EXCLUDED.parent_task_id,
                        items=EXCLUDED.items,
                        task_structure=EXCLUDED.task_structure,
                        coordination_topology=EXCLUDED.coordination_topology,
                        status=EXCLUDED.status,
                        forecast_id=EXCLUDED.forecast_id,
                        version=EXCLUDED.version,
                        created_at=EXCLUDED.created_at,
                        updated_at=EXCLUDED.updated_at
                    """,
                    self._row_params(plan),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save plan {plan.id!r}"
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
            filter_spec: Optional ``status`` / ``project`` / ``objective_id``
                filters.
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
