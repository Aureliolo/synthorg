"""SQLite repository for TrainingPlan persistence.

Provides ``SQLiteTrainingPlanRepository`` which persists
``TrainingPlan`` models via aiosqlite with upsert semantics.
"""

import asyncio
import contextlib
import json
import sqlite3
from datetime import UTC, datetime

import aiosqlite
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

logger = get_logger(__name__)

_UPSERT_SQL = """\
INSERT INTO training_plans (
    id, new_agent_id, new_agent_role, new_agent_level,
    new_agent_department, source_selector_type,
    enabled_content_types, curation_strategy_type,
    volume_caps, override_sources, skip_training,
    require_review, status, created_at, executed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    new_agent_id=excluded.new_agent_id,
    new_agent_role=excluded.new_agent_role,
    new_agent_level=excluded.new_agent_level,
    new_agent_department=excluded.new_agent_department,
    source_selector_type=excluded.source_selector_type,
    enabled_content_types=excluded.enabled_content_types,
    curation_strategy_type=excluded.curation_strategy_type,
    volume_caps=excluded.volume_caps,
    override_sources=excluded.override_sources,
    skip_training=excluded.skip_training,
    require_review=excluded.require_review,
    status=excluded.status,
    executed_at=excluded.executed_at"""


def _serialize_content_types(
    content_types: frozenset[ContentType],
) -> str:
    """Serialize enabled content types to a JSON array."""
    return json.dumps(sorted(ct.value for ct in content_types))


def _serialize_volume_caps(
    caps: tuple[tuple[ContentType, int], ...],
) -> str:
    """Serialize volume caps to a JSON array of ``[type, count]`` pairs."""
    return json.dumps([[ct.value, count] for ct, count in caps])


def _serialize_sources(
    sources: tuple[NotBlankStr, ...],
) -> str:
    """Serialize override sources to a JSON array."""
    return json.dumps([str(s) for s in sources])


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
        _serialize_content_types(plan.enabled_content_types),
        str(plan.curation_strategy_type),
        _serialize_volume_caps(plan.volume_caps),
        _serialize_sources(plan.override_sources),
        int(plan.skip_training),
        int(plan.require_review),
        plan.status.value,
        plan.created_at.astimezone(UTC).isoformat(),
        plan.executed_at.astimezone(UTC).isoformat()
        if plan.executed_at is not None
        else None,
    )


def _row_to_plan(row: aiosqlite.Row) -> TrainingPlan:
    """Reconstruct a ``TrainingPlan`` from a database row.

    Args:
        row: A single database row.

    Returns:
        Validated ``TrainingPlan`` model instance.

    Raises:
        QueryError: If deserialization fails.
    """
    data = dict(row)
    try:
        data["new_agent_level"] = SeniorityLevel(data["new_agent_level"])
        data["enabled_content_types"] = frozenset(
            ContentType(ct) for ct in json.loads(data["enabled_content_types"])
        )
        data["volume_caps"] = tuple(
            (ContentType(ct), count) for ct, count in json.loads(data["volume_caps"])
        )
        data["override_sources"] = tuple(
            NotBlankStr(s) for s in json.loads(data["override_sources"])
        )
        data["skip_training"] = bool(data["skip_training"])
        data["require_review"] = bool(data["require_review"])
        data["status"] = TrainingPlanStatus(data["status"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data["executed_at"] is not None:
            data["executed_at"] = datetime.fromisoformat(
                data["executed_at"],
            )
        return TrainingPlan.model_validate(data)
    except (
        json.JSONDecodeError,
        ValueError,
        TypeError,
        KeyError,
        ValidationError,
    ) as exc:
        plan_id = data.get("id", "<unknown>")
        msg = f"Failed to deserialize training plan {plan_id!r}"
        logger.warning(
            HR_TRAINING_PERSISTENCE_ERROR,
            plan_id=str(plan_id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


class SQLiteTrainingPlanRepository:
    """SQLite-backed training plan repository.

    Provides upsert-based persistence for ``TrainingPlan`` models
    using a shared ``aiosqlite.Connection``.

    Args:
        db: An open aiosqlite connection with ``row_factory``
            set to ``aiosqlite.Row``.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        # Inject the shared backend write lock so writes from this repo
        # serialize with sibling repos that share the same
        # ``aiosqlite.Connection``; fall back to a private lock for
        # standalone test construction.
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def save(self, plan: TrainingPlan) -> None:
        """Persist a training plan via upsert.

        Args:
            plan: Training plan to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_lock:
            try:
                await self._db.execute(_UPSERT_SQL, _plan_to_params(plan))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
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
            cursor = await self._db.execute(
                "SELECT * FROM training_plans WHERE id = ?",
                (str(plan_id),),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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

    async def latest_pending(
        self,
        agent_id: NotBlankStr,
    ) -> TrainingPlan | None:
        """Return the most recently created PENDING plan for an agent.

        Args:
            agent_id: Target agent identifier.

        Returns:
            The latest pending plan, or ``None`` if none exist.
        """
        try:
            cursor = await self._db.execute(
                """\
SELECT * FROM training_plans
WHERE new_agent_id = ? AND status = 'pending'
ORDER BY created_at DESC
LIMIT 1""",
                (str(agent_id),),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            QueryError: If the underlying SQLite query fails.
        """
        try:
            cursor = await self._db.execute(
                # ``id DESC`` breaks ties deterministically when two
                # plans share ``created_at`` -- plan IDs are UUIDs so
                # the ordering is arbitrary but stable.
                """\
SELECT * FROM training_plans
WHERE new_agent_id = ?
ORDER BY created_at DESC, id DESC
LIMIT 1""",
                (str(agent_id),),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
    ) -> tuple[TrainingPlan, ...]:
        """Return all plans for an agent ordered by created_at desc.

        Args:
            agent_id: Target agent identifier.

        Returns:
            Tuple of plans (may be empty).
        """
        try:
            cursor = await self._db.execute(
                """\
SELECT * FROM training_plans
WHERE new_agent_id = ?
ORDER BY created_at DESC""",
                (str(agent_id),),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to list plans for {agent_id!r}"
            logger.warning(
                HR_TRAINING_PERSISTENCE_ERROR,
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_row_to_plan(row) for row in rows)
