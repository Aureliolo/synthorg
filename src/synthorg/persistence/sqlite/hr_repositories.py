"""SQLite repository implementations for HR entities.

LifecycleEvent, TaskMetric, and CollaborationMetric repositories.
"""

import json
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.hr.enums import LifecycleEventType  # noqa: TC001
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
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

if TYPE_CHECKING:
    from pydantic import AwareDatetime

logger = get_logger(__name__)


class SQLiteLifecycleEventRepository:
    """SQLite implementation of the LifecycleEventRepository protocol.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def save(self, event: AgentLifecycleEvent) -> None:
        """Persist a lifecycle event."""
        async with self._write_context():
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
                logger.warning(
                    PERSISTENCE_LIFECYCLE_EVENT_SAVE_FAILED,
                    event_id=str(event.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    def _row_to_event(self, row: aiosqlite.Row) -> AgentLifecycleEvent:
        """Reconstruct a lifecycle event from a database row."""
        try:
            data = dict(row)
            data["metadata"] = json.loads(data["metadata"])
            return AgentLifecycleEvent.model_validate(data)
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
            event_id = row["id"] if row else "unknown"
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
        since: AwareDatetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[AgentLifecycleEvent, ...]:
        """List lifecycle events with optional filters.

        Bounded by *limit* (default :data:`DEFAULT_LIST_LIMIT`).
        """
        clauses: list[str] = []
        params: list[str | int] = []
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
        # This repository uses limit-only pagination; offset=0 is a
        # deliberate placeholder so the shared validator runs its
        # type-check and bounds-check on the limit value. SQLite's
        # ``LIMIT -1`` idiom would silently lift the cap, and Postgres
        # rejects negative LIMIT outright -- the shared validator
        # blocks both ahead of the DB call so the two backends fail
        # identically on a bad caller.
        limit = validate_pagination_args(
            limit, offset=0, event=PERSISTENCE_LIFECYCLE_EVENT_LIST_FAILED
        )
        sql += " LIMIT ?"
        params.append(limit)

        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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


class SQLiteTaskMetricRepository:
    """SQLite implementation of the TaskMetricRepository protocol.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def save(self, record: TaskMetricRecord) -> None:
        """Persist a task metric record."""
        async with self._write_context():
            try:
                data = record.model_dump(mode="json")
                await self._db.execute(
                    """\
INSERT INTO task_metrics (
    id, agent_id, task_id, task_type, completed_at,
    is_success, duration_seconds, cost, currency, turns_used,
    tokens_used, quality_score, complexity
) VALUES (
    :id, :agent_id, :task_id, :task_type, :completed_at,
    :is_success, :duration_seconds, :cost, :currency, :turns_used,
    :tokens_used, :quality_score, :complexity
)""",
                    data,
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to save task metric {record.id!r}"
                logger.warning(
                    PERSISTENCE_TASK_METRIC_SAVE_FAILED,
                    metric_id=str(record.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    def _row_to_record(self, row: aiosqlite.Row) -> TaskMetricRecord:
        """Reconstruct a task metric record from a database row."""
        try:
            data = dict(row)
            return TaskMetricRecord.model_validate(data)
        except (ValidationError, KeyError, TypeError) as exc:
            metric_id = row["id"] if row else "unknown"
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
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[TaskMetricRecord, ...]:
        """Query task metric records with optional filters.

        Bounded by *limit* (default :data:`DEFAULT_LIST_LIMIT`).
        """
        limit = validate_pagination_args(
            limit, 0, event=PERSISTENCE_TASK_METRIC_QUERY_FAILED
        )
        clauses: list[str] = []
        params: list[object] = []
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
       is_success, duration_seconds, cost, currency, turns_used,
       tokens_used, quality_score, complexity
FROM task_metrics"""
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY completed_at DESC LIMIT ?"
        params.append(limit)

        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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


class SQLiteCollaborationMetricRepository:
    """SQLite implementation of the CollaborationMetricRepository protocol.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def save(self, record: CollaborationMetricRecord) -> None:
        """Persist a collaboration metric record."""
        async with self._write_context():
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
                logger.warning(
                    PERSISTENCE_COLLAB_METRIC_SAVE_FAILED,
                    metric_id=str(record.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

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
        since: AwareDatetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[CollaborationMetricRecord, ...]:
        """Query collaboration metric records with optional filters.

        Bounded by *limit* (default :data:`DEFAULT_LIST_LIMIT`).
        """
        limit = validate_pagination_args(
            limit, 0, event=PERSISTENCE_COLLAB_METRIC_QUERY_FAILED
        )
        clauses: list[str] = []
        params: list[object] = []
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
        sql += " ORDER BY recorded_at DESC LIMIT ?"
        params.append(limit)

        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
