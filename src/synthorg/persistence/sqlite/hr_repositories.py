"""SQLite repository implementations for HR entities.

LifecycleEvent, TaskMetric, and CollaborationMetric repositories.
"""

import json
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import ValidationError

from synthorg.hr.enums import LifecycleEventType  # noqa: TC001
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,
    TaskMetricRecord,
)
from synthorg.observability import get_logger
from synthorg.observability.events.persistence import (
    PERSISTENCE_COLLAB_METRIC_DESERIALIZE_FAILED,
    PERSISTENCE_COLLAB_METRIC_QUERIED,
    PERSISTENCE_COLLAB_METRIC_QUERY_FAILED,
    PERSISTENCE_COLLAB_METRIC_SAVE_FAILED,
    PERSISTENCE_COLLAB_METRIC_SAVED,
    PERSISTENCE_LIFECYCLE_EVENT_DESERIALIZE_FAILED,
    PERSISTENCE_LIFECYCLE_EVENT_LIST_FAILED,
    PERSISTENCE_LIFECYCLE_EVENT_LISTED,
    PERSISTENCE_LIFECYCLE_EVENT_SAVE_FAILED,
    PERSISTENCE_LIFECYCLE_EVENT_SAVED,
    PERSISTENCE_TASK_METRIC_DESERIALIZE_FAILED,
    PERSISTENCE_TASK_METRIC_QUERIED,
    PERSISTENCE_TASK_METRIC_QUERY_FAILED,
    PERSISTENCE_TASK_METRIC_SAVE_FAILED,
    PERSISTENCE_TASK_METRIC_SAVED,
)
from synthorg.persistence.errors import QueryError

if TYPE_CHECKING:
    from pydantic import AwareDatetime

logger = get_logger(__name__)


class SQLiteLifecycleEventRepository:
    """SQLite implementation of the LifecycleEventRepository protocol.

    Args:
        db: An open aiosqlite connection.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, event: AgentLifecycleEvent) -> None:
        """Persist a lifecycle event."""
        try:
            data = event.model_dump(mode="json")
            await self._db.execute(
                """\
INSERT INTO lifecycle_events (
    id, agent_id, agent_name, event_type, timestamp,
    initiated_by, details, metadata
) VALUES (
    :id, :agent_id, :agent_name, :event_type, :timestamp,
    :initiated_by, :details, :metadata
)""",
                {**data, "metadata": json.dumps(data["metadata"])},
            )
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to save lifecycle event {event.id!r}"
            logger.exception(
                PERSISTENCE_LIFECYCLE_EVENT_SAVE_FAILED,
                event_id=str(event.id),
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_LIFECYCLE_EVENT_SAVED,
            event_id=str(event.id),
        )

    def _row_to_event(self, row: aiosqlite.Row) -> AgentLifecycleEvent:
        """Reconstruct a lifecycle event from a database row."""
        try:
            data = dict(row)
            data["metadata"] = json.loads(data["metadata"])
            return AgentLifecycleEvent.model_validate(data)
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
            event_id = row["id"] if row else "unknown"
            msg = f"Failed to deserialize lifecycle event {event_id!r}"
            logger.exception(
                PERSISTENCE_LIFECYCLE_EVENT_DESERIALIZE_FAILED,
                event_id=event_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc

    async def list_events(
        self,
        *,
        agent_id: str | None = None,
        event_type: LifecycleEventType | None = None,
        since: AwareDatetime | None = None,
    ) -> tuple[AgentLifecycleEvent, ...]:
        """List lifecycle events with optional filters."""
        clauses: list[str] = []
        params: list[str] = []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type.value)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())

        sql = """\
SELECT id, agent_id, agent_name, event_type, timestamp,
       initiated_by, details, metadata
FROM lifecycle_events"""
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC"

        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list lifecycle events"
            logger.exception(
                PERSISTENCE_LIFECYCLE_EVENT_LIST_FAILED,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        events = tuple(self._row_to_event(row) for row in rows)
        logger.debug(PERSISTENCE_LIFECYCLE_EVENT_LISTED, count=len(events))
        return events


class SQLiteTaskMetricRepository:
    """SQLite implementation of the TaskMetricRepository protocol.

    Args:
        db: An open aiosqlite connection.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, record: TaskMetricRecord) -> None:
        """Persist a task metric record."""
        try:
            data = record.model_dump(mode="json")
            await self._db.execute(
                """\
INSERT INTO task_metrics (
    id, agent_id, task_id, task_type, completed_at,
    is_success, duration_seconds, cost_usd, turns_used,
    tokens_used, quality_score, complexity
) VALUES (
    :id, :agent_id, :task_id, :task_type, :completed_at,
    :is_success, :duration_seconds, :cost_usd, :turns_used,
    :tokens_used, :quality_score, :complexity
)""",
                data,
            )
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to save task metric {record.id!r}"
            logger.exception(
                PERSISTENCE_TASK_METRIC_SAVE_FAILED,
                metric_id=str(record.id),
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_TASK_METRIC_SAVED,
            metric_id=str(record.id),
        )

    def _row_to_record(self, row: aiosqlite.Row) -> TaskMetricRecord:
        """Reconstruct a task metric record from a database row."""
        try:
            data = dict(row)
            return TaskMetricRecord.model_validate(data)
        except (ValidationError, KeyError, TypeError) as exc:
            metric_id = row["id"] if row else "unknown"
            msg = f"Failed to deserialize task metric {metric_id!r}"
            logger.exception(
                PERSISTENCE_TASK_METRIC_DESERIALIZE_FAILED,
                metric_id=metric_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        *,
        agent_id: str | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
    ) -> tuple[TaskMetricRecord, ...]:
        """Query task metric records with optional filters."""
        clauses: list[str] = []
        params: list[str] = []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if since is not None:
            clauses.append("completed_at >= ?")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("completed_at <= ?")
            params.append(until.isoformat())

        sql = """\
SELECT id, agent_id, task_id, task_type, completed_at,
       is_success, duration_seconds, cost_usd, turns_used,
       tokens_used, quality_score, complexity
FROM task_metrics"""
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY completed_at DESC"

        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query task metrics"
            logger.exception(
                PERSISTENCE_TASK_METRIC_QUERY_FAILED,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        records = tuple(self._row_to_record(row) for row in rows)
        logger.debug(PERSISTENCE_TASK_METRIC_QUERIED, count=len(records))
        return records


class SQLiteCollaborationMetricRepository:
    """SQLite implementation of the CollaborationMetricRepository protocol.

    Args:
        db: An open aiosqlite connection.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, record: CollaborationMetricRecord) -> None:
        """Persist a collaboration metric record."""
        try:
            data = record.model_dump(mode="json")
            await self._db.execute(
                """\
INSERT INTO collaboration_metrics (
    id, agent_id, recorded_at, delegation_success,
    delegation_response_seconds, conflict_constructiveness,
    meeting_contribution, loop_triggered, handoff_completeness
) VALUES (
    :id, :agent_id, :recorded_at, :delegation_success,
    :delegation_response_seconds, :conflict_constructiveness,
    :meeting_contribution, :loop_triggered, :handoff_completeness
)""",
                data,
            )
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to save collaboration metric {record.id!r}"
            logger.exception(
                PERSISTENCE_COLLAB_METRIC_SAVE_FAILED,
                metric_id=str(record.id),
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_COLLAB_METRIC_SAVED,
            metric_id=str(record.id),
        )

    def _row_to_record(self, row: aiosqlite.Row) -> CollaborationMetricRecord:
        """Reconstruct a collaboration metric record from a database row."""
        try:
            data = dict(row)
            # Convert SQLite integer booleans.
            if data.get("delegation_success") is not None:
                data["delegation_success"] = bool(data["delegation_success"])
            data["loop_triggered"] = bool(data["loop_triggered"])
            return CollaborationMetricRecord.model_validate(data)
        except (ValidationError, KeyError, TypeError) as exc:
            metric_id = row["id"] if row else "unknown"
            msg = f"Failed to deserialize collaboration metric {metric_id!r}"
            logger.exception(
                PERSISTENCE_COLLAB_METRIC_DESERIALIZE_FAILED,
                metric_id=metric_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        *,
        agent_id: str | None = None,
        since: AwareDatetime | None = None,
    ) -> tuple[CollaborationMetricRecord, ...]:
        """Query collaboration metric records with optional filters."""
        clauses: list[str] = []
        params: list[str] = []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if since is not None:
            clauses.append("recorded_at >= ?")
            params.append(since.isoformat())

        sql = """\
SELECT id, agent_id, recorded_at, delegation_success,
       delegation_response_seconds, conflict_constructiveness,
       meeting_contribution, loop_triggered, handoff_completeness
FROM collaboration_metrics"""
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY recorded_at DESC"

        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query collaboration metrics"
            logger.exception(
                PERSISTENCE_COLLAB_METRIC_QUERY_FAILED,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        records = tuple(self._row_to_record(row) for row in rows)
        logger.debug(PERSISTENCE_COLLAB_METRIC_QUERIED, count=len(records))
        return records
