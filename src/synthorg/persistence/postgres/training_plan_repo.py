"""Postgres repository for TrainingPlan persistence.

Postgres-native port of the SQLite training plan repository.  Uses
JSONB for array/object columns and native TIMESTAMPTZ for timestamps.
"""

from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.core.enums import SeniorityLevel
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.hr.training.models import (
    ContentType,
    TrainingPlan,
    TrainingPlanStatus,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.training import (
    HR_TRAINING_PERSISTENCE_ERROR,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.training_protocol import TrainingPlanFilterSpec  # noqa: TC001

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


def _row_to_plan(row: dict[str, Any]) -> TrainingPlan:
    """Reconstruct a ``TrainingPlan`` from a Postgres dict_row.

    Postgres returns JSONB as Python lists, TIMESTAMPTZ as aware
    datetimes, and BOOLEAN as bool -- minimal conversion needed.

    Raises:
        QueryError: If deserialization fails.
    """
    data = dict(row)
    try:
        data["new_agent_level"] = SeniorityLevel(
            data["new_agent_level"],
        )
        data["enabled_content_types"] = frozenset(
            ContentType(ct) for ct in data["enabled_content_types"]
        )
        data["volume_caps"] = tuple(
            (ContentType(ct), count) for ct, count in data["volume_caps"]
        )
        data["override_sources"] = tuple(
            NotBlankStr(s) for s in data["override_sources"]
        )
        data["status"] = TrainingPlanStatus(data["status"])
        return TrainingPlan.model_validate(data)
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        plan_id = data.get("id", "<unknown>")
        msg = f"Failed to deserialize training plan {plan_id!r}"
        logger.warning(
            HR_TRAINING_PERSISTENCE_ERROR,
            plan_id=str(plan_id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


_UPSERT_SQL = """\
INSERT INTO training_plans (
    id, new_agent_id, new_agent_role, new_agent_level,
    new_agent_department, source_selector_type,
    enabled_content_types, curation_strategy_type,
    volume_caps, override_sources, skip_training,
    require_review, status, created_at, executed_at
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT(id) DO UPDATE SET
    new_agent_id=EXCLUDED.new_agent_id,
    new_agent_role=EXCLUDED.new_agent_role,
    new_agent_level=EXCLUDED.new_agent_level,
    new_agent_department=EXCLUDED.new_agent_department,
    source_selector_type=EXCLUDED.source_selector_type,
    enabled_content_types=EXCLUDED.enabled_content_types,
    curation_strategy_type=EXCLUDED.curation_strategy_type,
    volume_caps=EXCLUDED.volume_caps,
    override_sources=EXCLUDED.override_sources,
    skip_training=EXCLUDED.skip_training,
    require_review=EXCLUDED.require_review,
    status=EXCLUDED.status,
    executed_at=EXCLUDED.executed_at"""


def _plan_to_params(plan: TrainingPlan) -> tuple[object, ...]:
    """Build the parameter tuple for the upsert SQL statement."""
    return (
        str(plan.id),
        str(plan.new_agent_id),
        str(plan.new_agent_role),
        plan.new_agent_level.value,
        str(plan.new_agent_department)
        if plan.new_agent_department is not None
        else None,
        str(plan.source_selector_type),
        Jsonb(sorted(ct.value for ct in plan.enabled_content_types)),
        str(plan.curation_strategy_type),
        Jsonb([[ct.value, count] for ct, count in plan.volume_caps]),
        Jsonb([str(s) for s in plan.override_sources]),
        plan.skip_training,
        plan.require_review,
        plan.status.value,
        plan.created_at,
        plan.executed_at,
    )


class PostgresTrainingPlanRepository:
    """Postgres-backed training plan repository.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, plan: TrainingPlan) -> None:
        """Persist a training plan via upsert.

        Args:
            plan: Training plan to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor() as cur,
            ):
                await cur.execute(
                    _UPSERT_SQL,
                    _plan_to_params(plan),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save training plan {plan.id!r}"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                plan_id=str(plan.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(
        self,
        plan_id: NotBlankStr,
    ) -> TrainingPlan | None:
        """Retrieve a training plan by ID.

        Args:
            plan_id: Training plan identifier.

        Returns:
            The plan, or ``None`` if not found.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM training_plans WHERE id = %s",
                    (str(plan_id),),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch training plan {plan_id!r}"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                plan_id=str(plan_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return _row_to_plan(row)

    async def delete(
        self,
        plan_id: NotBlankStr,
    ) -> bool:
        """Delete a training plan by ID.

        Args:
            plan_id: Training plan identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the operation fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "DELETE FROM training_plans WHERE id = %s",
                    (str(plan_id),),
                )
                await conn.commit()
                return cur.rowcount > 0
        except psycopg.Error as exc:
            msg = f"Failed to delete training plan {plan_id!r}"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                plan_id=str(plan_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[TrainingPlan, ...]:
        """List training plans with pagination (ADR-0001).

        Args:
            limit: Maximum plans to return (must be >= 1).
            offset: Rows to skip before the window.

        Returns:
            Tuple of plans ordered by id ascending.

        Raises:
            QueryError: If the operation fails.
        """
        limit = validate_pagination_args(limit, 0, event=HR_TRAINING_PERSISTENCE_ERROR)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM training_plans ORDER BY id ASC LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list training plans"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_row_to_plan(row) for row in rows)

    async def query(
        self,
        filter_spec: TrainingPlanFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[TrainingPlan, ...]:
        """Query training plans matching the filter spec (ADR-0001).

        Args:
            filter_spec: Carries optional filters for agent_id, status.
            limit: Maximum plans to return (must be >= 1).
            offset: Rows to skip before the window.

        Returns:
            Tuple of matching plans ordered by id ascending.

        Raises:
            QueryError: If the operation fails.
        """
        limit = validate_pagination_args(limit, 0, event=HR_TRAINING_PERSISTENCE_ERROR)
        try:
            where_clauses = []
            params: list[object] = []
            if filter_spec.agent_id is not None:
                where_clauses.append("new_agent_id = %s")
                params.append(str(filter_spec.agent_id))
            if filter_spec.status is not None:
                where_clauses.append("status = %s")
                params.append(filter_spec.status)

            where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
            sql = (
                f"SELECT * FROM training_plans WHERE {where_clause} "  # noqa: S608
                "ORDER BY id ASC LIMIT %s OFFSET %s"
            )
            params.extend([limit, offset])

            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query training plans"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_row_to_plan(row) for row in rows)

    async def count(self, filter_spec: TrainingPlanFilterSpec) -> int:
        """Count training plans matching the filter spec (ADR-0001).

        Args:
            filter_spec: Carries optional filters for agent_id, status.

        Returns:
            Total number of matching plans.

        Raises:
            QueryError: If the operation fails.
        """
        try:
            where_clauses = []
            params: list[object] = []
            if filter_spec.agent_id is not None:
                where_clauses.append("new_agent_id = %s")
                params.append(str(filter_spec.agent_id))
            if filter_spec.status is not None:
                where_clauses.append("status = %s")
                params.append(filter_spec.status)

            where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
            sql = f"SELECT COUNT(*) as cnt FROM training_plans WHERE {where_clause}"  # noqa: S608

            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                row = await cur.fetchone()
                return int(row["cnt"]) if row else 0
        except psycopg.Error as exc:
            msg = "Failed to count training plans"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def latest_pending(
        self,
        agent_id: NotBlankStr,
    ) -> TrainingPlan | None:
        """Return the most recently created PENDING plan for an agent.

        Args:
            agent_id: Agent identifier.

        Returns:
            The latest pending plan, or ``None`` if none exists.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    """\
SELECT * FROM training_plans
WHERE new_agent_id = %s AND status = 'pending'
ORDER BY created_at DESC
LIMIT 1""",
                    (str(agent_id),),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch latest pending plan for {agent_id!r}"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return _row_to_plan(row)

    async def latest_by_agent(
        self,
        agent_id: NotBlankStr,
    ) -> TrainingPlan | None:
        """Return the most recently created plan for an agent (any status).

        Args:
            agent_id: Target agent identifier.

        Returns:
            The latest plan (by ``created_at`` DESC, then ``id`` DESC),
            or ``None`` if the agent has no plans yet.

        Raises:
            QueryError: If the underlying Postgres query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    # ``id DESC`` breaks ties deterministically when two
                    # plans share ``created_at`` -- plan IDs are UUIDs
                    # so the ordering is arbitrary but stable.
                    """\
SELECT * FROM training_plans
WHERE new_agent_id = %s
ORDER BY created_at DESC, id DESC
LIMIT 1""",
                    (str(agent_id),),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch latest plan for {agent_id!r}"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return _row_to_plan(row)

    async def list_by_agent(
        self,
        agent_id: NotBlankStr,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[TrainingPlan, ...]:
        """Return plans for an agent ordered by created_at desc.

        Args:
            agent_id: Agent identifier.
            limit: Maximum plans to return (must be >= 1,
                default :data:`DEFAULT_PAGE_SIZE`).

        Returns:
            Tuple of plans ordered by ``created_at`` descending,
            capped at *limit* rows.
        """
        limit = validate_pagination_args(limit, 0, event=HR_TRAINING_PERSISTENCE_ERROR)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    """\
SELECT * FROM training_plans
WHERE new_agent_id = %s
ORDER BY created_at DESC, id DESC LIMIT %s""",
                    (str(agent_id), limit),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Failed to list plans for {agent_id!r}"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_row_to_plan(row) for row in rows)
