"""SQLite repository implementation for Plan."""

import json
import sqlite3
from uuid import UUID

import aiosqlite
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
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.plan_protocol import PlanFilterSpec
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000

_COLUMNS = (
    "id, project, objective_id, parent_task_id, items, task_structure, "
    "coordination_topology, status, forecast_id, version, created_at, updated_at"
)


def _row_to_plan(row: aiosqlite.Row) -> Plan:
    """Reconstruct a ``Plan`` from a database row.

    Returns:
        Validated ``Plan`` model instance.
    """
    data = dict(row)
    data["items"] = tuple(
        PlanItem.model_validate(item) for item in json.loads(data["items"])
    )
    data["task_structure"] = TaskStructure(data["task_structure"])
    data["coordination_topology"] = CoordinationTopology(data["coordination_topology"])
    data["status"] = PlanStatus(data["status"])
    forecast_id = data["forecast_id"]
    data["forecast_id"] = UUID(forecast_id) if forecast_id else None
    data["created_at"] = coerce_row_timestamp(data["created_at"])
    data["updated_at"] = coerce_row_timestamp(data["updated_at"])
    return Plan.model_validate(data)


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

    @staticmethod
    def _row_params(plan: Plan) -> tuple[object, ...]:
        """Serialise a plan into positional SQL params in ``_COLUMNS`` order.

        Returns:
            Scalar param values for INSERT/UPDATE, aligned with ``_COLUMNS``.
        """
        return (
            str(plan.id),
            plan.project,
            plan.objective_id,
            plan.parent_task_id,
            json.dumps([item.model_dump(mode="json") for item in plan.items]),
            plan.task_structure.value,
            plan.coordination_topology.value,
            plan.status.value,
            str(plan.forecast_id) if plan.forecast_id is not None else None,
            plan.version,
            format_iso_utc(plan.created_at),
            format_iso_utc(plan.updated_at),
        )

    async def create(self, plan: Plan) -> None:
        """Insert a new plan, failing if the id already exists.

        Raises:
            DuplicateRecordError: A plan with the same id exists.
            QueryError: If the database operation fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    f"INSERT INTO plans ({_COLUMNS}) "  # noqa: S608
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    self._row_params(plan),
                )
                await self._db.commit()
            except (sqlite3.IntegrityError, aiosqlite.IntegrityError) as exc:
                await self._safe_rollback()
                logger.warning(
                    PERSISTENCE_PLAN_SAVE_FAILED,
                    plan_id=str(plan.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                if is_unique_constraint_error(exc):
                    msg = f"Plan with id {plan.id!r} already exists"
                    raise DuplicateRecordError(msg) from exc
                msg = f"Failed to create plan {plan.id!r}"
                raise QueryError(msg) from exc
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
        params: list[object] = [*self._row_params(plan)[1:], str(plan.id)]
        guard = ""
        if expected_version is not None:
            guard = " AND version=?"
            params.append(expected_version)
        async with self._write_context():
            try:
                async with self._db.execute(
                    f"""\
UPDATE plans SET
    project=?,
    objective_id=?,
    parent_task_id=?,
    items=?,
    task_structure=?,
    coordination_topology=?,
    status=?,
    forecast_id=?,
    version=?,
    created_at=?,
    updated_at=?
WHERE id=?{guard}""",  # noqa: S608 -- guard is a fixed literal, values parameterized
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
            QueryError: If the database operation fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    f"INSERT INTO plans ({_COLUMNS}) "  # noqa: S608
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "project=excluded.project, "
                    "objective_id=excluded.objective_id, "
                    "parent_task_id=excluded.parent_task_id, "
                    "items=excluded.items, "
                    "task_structure=excluded.task_structure, "
                    "coordination_topology=excluded.coordination_topology, "
                    "status=excluded.status, "
                    "forecast_id=excluded.forecast_id, "
                    "version=excluded.version, "
                    "created_at=excluded.created_at, "
                    "updated_at=excluded.updated_at",
                    self._row_params(plan),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
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
            plan = _row_to_plan(row)
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
            plans = tuple(_row_to_plan(row) for row in rows)
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
