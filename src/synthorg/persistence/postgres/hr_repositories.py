"""Postgres repository implementations for HR entities.

Postgres-native port of ``synthorg.persistence.sqlite.hr_repositories``.
LifecycleEvent, TaskMetric, and CollaborationMetric repositories.
Uses native JSONB for metadata fields, native TIMESTAMPTZ for all timestamps,
and native BOOLEAN for boolean flags. The protocol surface returns the same
Pydantic models as the SQLite backend.
"""

from datetime import datetime
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.hr.enums import LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,
    TaskMetricRecord,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_COLLAB_METRIC_DESERIALIZE_FAILED,
    PERSISTENCE_COLLAB_METRIC_QUERIED,
    PERSISTENCE_COLLAB_METRIC_QUERY_FAILED,
    PERSISTENCE_COLLAB_METRIC_SAVE_FAILED,
    PERSISTENCE_LIFECYCLE_EVENT_DESERIALIZE_FAILED,
    PERSISTENCE_LIFECYCLE_EVENT_LIST_FAILED,
    PERSISTENCE_LIFECYCLE_EVENT_LISTED,
    PERSISTENCE_LIFECYCLE_EVENT_SAVE_FAILED,
    PERSISTENCE_TASK_METRIC_DESERIALIZE_FAILED,
    PERSISTENCE_TASK_METRIC_QUERIED,
    PERSISTENCE_TASK_METRIC_QUERY_FAILED,
    PERSISTENCE_TASK_METRIC_SAVE_FAILED,
)
from synthorg.persistence._shared.pagination import (
    DEFAULT_LIST_LIMIT,
    validate_pagination_args,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


class PostgresLifecycleEventRepository:
    """Postgres implementation of the LifecycleEventRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, event: AgentLifecycleEvent) -> None:
        """Persist a lifecycle event.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            data = event.model_dump(mode="json")
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO lifecycle_events (
                        id, agent_id, agent_name, event_type, timestamp,
                        initiated_by, details, metadata
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        data["id"],
                        data["agent_id"],
                        data["agent_name"],
                        data["event_type"],
                        data["timestamp"],
                        data["initiated_by"],
                        data["details"],
                        Jsonb(data["metadata"]),
                    ),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save lifecycle event {event.id!r}"
            logger.warning(
                PERSISTENCE_LIFECYCLE_EVENT_SAVE_FAILED,
                event_id=str(event.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    def _row_to_event(self, row: DictRow) -> AgentLifecycleEvent:
        """Reconstruct a lifecycle event from a database row.

        Returns:
            Result of type ``AgentLifecycleEvent``.

        Raises:
            QueryError: If deserialization or validation fails.
        """
        try:
            data = dict(row)
            # Postgres returns JSONB as dict directly, no json.loads needed
            return AgentLifecycleEvent.model_validate(data)
        except (ValidationError, KeyError, TypeError) as exc:
            event_id = row.get("id") if row else "unknown"
            msg = f"Failed to deserialize lifecycle event {event_id!r}"
            logger.warning(
                PERSISTENCE_LIFECYCLE_EVENT_DESERIALIZE_FAILED,
                event_id=event_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def list_events(
        self,
        *,
        agent_id: str | None = None,
        event_type: LifecycleEventType | None = None,
        since: datetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[AgentLifecycleEvent, ...]:
        """List lifecycle events with optional filters.

        Bounded by *limit* (default :data:`DEFAULT_LIST_LIMIT`).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        clauses: list[str] = []
        params: list[object] = []
        if agent_id is not None:
            clauses.append("agent_id = %s")
            params.append(agent_id)
        if event_type is not None:
            clauses.append("event_type = %s")
            params.append(event_type.value)
        if since is not None:
            clauses.append("timestamp >= %s")
            params.append(since)

        sql = """\
SELECT id, agent_id, agent_name, event_type, timestamp,
       initiated_by, details, metadata
FROM lifecycle_events"""
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC"
        # This repository uses limit-only pagination; offset=0 is a
        # deliberate placeholder so the shared validator runs its
        # type-check and bounds-check on the limit value.
        limit = validate_pagination_args(
            limit, offset=0, event=PERSISTENCE_LIFECYCLE_EVENT_LIST_FAILED
        )
        sql += " LIMIT %s"
        params.append(limit)

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list lifecycle events"
            logger.warning(
                PERSISTENCE_LIFECYCLE_EVENT_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        events = tuple(self._row_to_event(row) for row in rows)
        logger.debug(PERSISTENCE_LIFECYCLE_EVENT_LISTED, count=len(events))
        return events


class PostgresTaskMetricRepository:
    """Postgres implementation of the TaskMetricRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, record: TaskMetricRecord) -> None:
        """Persist a task metric record.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            data = record.model_dump(mode="json")
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO task_metrics (
                        id, agent_id, task_id, task_type, completed_at,
                        is_success, duration_seconds, cost, currency,
                        turns_used, tokens_used, quality_score, complexity
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        data["id"],
                        data["agent_id"],
                        data["task_id"],
                        data["task_type"],
                        data["completed_at"],
                        data["is_success"],
                        data["duration_seconds"],
                        data["cost"],
                        data["currency"],
                        data["turns_used"],
                        data["tokens_used"],
                        data["quality_score"],
                        data["complexity"],
                    ),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save task metric {record.id!r}"
            logger.warning(
                PERSISTENCE_TASK_METRIC_SAVE_FAILED,
                metric_id=str(record.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    def _row_to_record(self, row: DictRow) -> TaskMetricRecord:
        """Reconstruct a task metric record from a database row.

        Returns:
            Result of type ``TaskMetricRecord``.

        Raises:
            QueryError: If deserialization or validation fails.
        """
        try:
            data = dict(row)
            return TaskMetricRecord.model_validate(data)
        except (ValidationError, KeyError, TypeError) as exc:
            metric_id = row.get("id") if row else "unknown"
            msg = f"Failed to deserialize task metric {metric_id!r}"
            logger.warning(
                PERSISTENCE_TASK_METRIC_DESERIALIZE_FAILED,
                metric_id=metric_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        *,
        agent_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[TaskMetricRecord, ...]:
        """Query task metric records with optional filters.

        Bounded by *limit* (default :data:`DEFAULT_LIST_LIMIT`).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, 0, event=PERSISTENCE_TASK_METRIC_QUERY_FAILED
        )
        clauses: list[str] = []
        params: list[object] = []
        if agent_id is not None:
            clauses.append("agent_id = %s")
            params.append(agent_id)
        if since is not None:
            clauses.append("completed_at >= %s")
            params.append(since)
        if until is not None:
            clauses.append("completed_at <= %s")
            params.append(until)

        sql = """\
SELECT id, agent_id, task_id, task_type, completed_at,
       is_success, duration_seconds, cost, currency, turns_used,
       tokens_used, quality_score, complexity
FROM task_metrics"""
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY completed_at DESC LIMIT %s"
        params.append(limit)

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query task metrics"
            logger.warning(
                PERSISTENCE_TASK_METRIC_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        records = tuple(self._row_to_record(row) for row in rows)
        logger.debug(PERSISTENCE_TASK_METRIC_QUERIED, count=len(records))
        return records


class PostgresCollaborationMetricRepository:
    """Postgres implementation of the CollaborationMetricRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, record: CollaborationMetricRecord) -> None:
        """Persist a collaboration metric record.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            data = record.model_dump(mode="json")
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO collaboration_metrics (
                        id, agent_id, recorded_at, delegation_success,
                        delegation_response_seconds, conflict_constructiveness,
                        meeting_contribution, loop_triggered, handoff_completeness
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        data["id"],
                        data["agent_id"],
                        data["recorded_at"],
                        data["delegation_success"],
                        data["delegation_response_seconds"],
                        data["conflict_constructiveness"],
                        data["meeting_contribution"],
                        data["loop_triggered"],
                        data["handoff_completeness"],
                    ),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save collaboration metric {record.id!r}"
            logger.warning(
                PERSISTENCE_COLLAB_METRIC_SAVE_FAILED,
                metric_id=str(record.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    def _row_to_record(self, row: DictRow) -> CollaborationMetricRecord:
        """Reconstruct a collaboration metric record from a database row.

        Returns:
            Result of type ``CollaborationMetricRecord``.

        Raises:
            QueryError: If deserialization or validation fails.
        """
        try:
            data = dict(row)
            # Postgres returns BOOLEAN as bool natively
            return CollaborationMetricRecord.model_validate(data)
        except (ValidationError, KeyError, TypeError) as exc:
            metric_id = row.get("id") if row else "unknown"
            msg = f"Failed to deserialize collaboration metric {metric_id!r}"
            logger.warning(
                PERSISTENCE_COLLAB_METRIC_DESERIALIZE_FAILED,
                metric_id=metric_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        *,
        agent_id: str | None = None,
        since: datetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[CollaborationMetricRecord, ...]:
        """Query collaboration metric records with optional filters.

        Bounded by *limit* (default :data:`DEFAULT_LIST_LIMIT`).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, 0, event=PERSISTENCE_COLLAB_METRIC_QUERY_FAILED
        )
        clauses: list[str] = []
        params: list[object] = []
        if agent_id is not None:
            clauses.append("agent_id = %s")
            params.append(agent_id)
        if since is not None:
            clauses.append("recorded_at >= %s")
            params.append(since)

        sql = """\
SELECT id, agent_id, recorded_at, delegation_success,
       delegation_response_seconds, conflict_constructiveness,
       meeting_contribution, loop_triggered, handoff_completeness
FROM collaboration_metrics"""
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY recorded_at DESC LIMIT %s"
        params.append(limit)

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query collaboration metrics"
            logger.warning(
                PERSISTENCE_COLLAB_METRIC_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        records = tuple(self._row_to_record(row) for row in rows)
        logger.debug(PERSISTENCE_COLLAB_METRIC_QUERIED, count=len(records))
        return records
