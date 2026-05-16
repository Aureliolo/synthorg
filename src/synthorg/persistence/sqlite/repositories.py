"""SQLite repository implementations for Task, CostRecord, and Message.

HR-related repositories (LifecycleEvent, TaskMetric, CollaborationMetric)
are in ``hr_repositories.py`` within this package.
"""

import json
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec
    from synthorg.persistence.message_protocol import MessageFilterSpec
    from synthorg.persistence.task_protocol import TaskFilterSpec
from pydantic import BaseModel, ValidationError

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.communication.message import Message
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
    PERSISTENCE_COST_RECORD_AGGREGATED,
    PERSISTENCE_COST_RECORD_QUERIED,
    PERSISTENCE_COST_RECORD_QUERY_FAILED,
    PERSISTENCE_COST_RECORD_SAVE_FAILED,
    PERSISTENCE_MESSAGE_DELETE_FAILED,
    PERSISTENCE_MESSAGE_DESERIALIZE_FAILED,
    PERSISTENCE_MESSAGE_DUPLICATE,
    PERSISTENCE_MESSAGE_HISTORY_FAILED,
    PERSISTENCE_MESSAGE_HISTORY_FETCHED,
    PERSISTENCE_MESSAGE_SAVE_FAILED,
    PERSISTENCE_TASK_COUNT_FAILED,
    PERSISTENCE_TASK_COUNTED,
    PERSISTENCE_TASK_DELETE_FAILED,
    PERSISTENCE_TASK_DESERIALIZE_FAILED,
    PERSISTENCE_TASK_FETCH_FAILED,
    PERSISTENCE_TASK_FETCHED,
    PERSISTENCE_TASK_LIST_FAILED,
    PERSISTENCE_TASK_LISTED,
    PERSISTENCE_TASK_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    DEFAULT_LIST_LIMIT,
    format_iso_utc,
    normalize_utc,
    validate_pagination_args,
)
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)

logger = get_logger(__name__)


def _json_list(items: tuple[object, ...]) -> str:
    """Serialize a tuple of Pydantic models or scalars to a JSON array.

    Items must be JSON-serializable or Pydantic models.
    Non-serializable items will raise ``TypeError``.
    """
    return json.dumps(
        [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in items
        ]
    )


class SQLiteTaskRepository:
    """SQLite implementation of the TaskRepository protocol.

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

    async def save(self, task: Task) -> None:
        """Persist a task (upsert semantics)."""
        async with self._write_context():
            try:
                params = task.model_dump(mode="json")
                # Tuple fields must be stored as JSON strings.
                params["reviewers"] = _json_list(task.reviewers)
                params["dependencies"] = _json_list(task.dependencies)
                params["artifacts_expected"] = _json_list(task.artifacts_expected)
                params["acceptance_criteria"] = _json_list(
                    task.acceptance_criteria,
                )
                params["delegation_chain"] = _json_list(task.delegation_chain)

                await self._db.execute(
                    """\
INSERT INTO tasks (
    id, title, description, type, priority, project, created_by,
    assigned_to, status, estimated_complexity, budget_limit, deadline,
    max_retries, parent_task_id, task_structure, coordination_topology,
    reviewers, dependencies, artifacts_expected, acceptance_criteria,
    delegation_chain
) VALUES (
    :id, :title, :description, :type, :priority, :project, :created_by,
    :assigned_to, :status, :estimated_complexity, :budget_limit, :deadline,
    :max_retries, :parent_task_id, :task_structure, :coordination_topology,
    :reviewers, :dependencies, :artifacts_expected, :acceptance_criteria,
    :delegation_chain
)
ON CONFLICT(id) DO UPDATE SET
    title=excluded.title,
    description=excluded.description,
    type=excluded.type,
    priority=excluded.priority,
    project=excluded.project,
    created_by=excluded.created_by,
    assigned_to=excluded.assigned_to,
    status=excluded.status,
    estimated_complexity=excluded.estimated_complexity,
    budget_limit=excluded.budget_limit,
    deadline=excluded.deadline,
    max_retries=excluded.max_retries,
    parent_task_id=excluded.parent_task_id,
    task_structure=excluded.task_structure,
    coordination_topology=excluded.coordination_topology,
    reviewers=excluded.reviewers,
    dependencies=excluded.dependencies,
    artifacts_expected=excluded.artifacts_expected,
    acceptance_criteria=excluded.acceptance_criteria,
    delegation_chain=excluded.delegation_chain
""",
                    params,
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to save task {task.id!r}"
                logger.warning(
                    PERSISTENCE_TASK_SAVE_FAILED,
                    task_id=task.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    #: Fields stored as JSON strings that need deserialization.
    _JSON_FIELDS: tuple[str, ...] = (
        "reviewers",
        "dependencies",
        "artifacts_expected",
        "acceptance_criteria",
        "delegation_chain",
    )

    def _row_to_task(self, row: aiosqlite.Row) -> Task:
        """Reconstruct a Task from a database row."""
        try:
            data = dict(row)
            for field in self._JSON_FIELDS:
                data[field] = json.loads(data[field])
            return Task.model_validate(data)
        except (
            json.JSONDecodeError,
            ValidationError,
            KeyError,
            TypeError,
        ) as exc:
            task_id = row["id"] if row else "unknown"
            msg = f"Failed to deserialize task {task_id!r}"
            logger.warning(
                PERSISTENCE_TASK_DESERIALIZE_FAILED,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    _TASK_COLUMNS = """\
id, title, description, type, priority, project, created_by,
       assigned_to, status, estimated_complexity, budget_limit, deadline,
       max_retries, parent_task_id, task_structure, coordination_topology,
       reviewers, dependencies, artifacts_expected, acceptance_criteria,
       delegation_chain"""

    async def get(self, task_id: str) -> Task | None:
        """Retrieve a task by its ID."""
        try:
            cursor = await self._db.execute(
                f"SELECT {self._TASK_COLUMNS} FROM tasks WHERE id = ?",  # noqa: S608
                (task_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch task {task_id!r}"
            logger.warning(
                PERSISTENCE_TASK_FETCH_FAILED,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(PERSISTENCE_TASK_FETCHED, task_id=task_id, found=False)
            return None
        logger.debug(PERSISTENCE_TASK_FETCHED, task_id=task_id, found=True)
        return self._row_to_task(row)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        """List tasks with pagination (no filters).

        Ordering is deterministic on the primary key ``id`` so paginated
        callers see stable windows.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_TASK_LIST_FAILED
        )
        query = (
            f"SELECT {self._TASK_COLUMNS} FROM tasks ORDER BY id ASC LIMIT ? OFFSET ?"  # noqa: S608
        )
        params: list[object] = [limit, offset]

        try:
            cursor = await self._db.execute(query, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list tasks"
            logger.warning(
                PERSISTENCE_TASK_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        tasks = tuple(self._row_to_task(row) for row in rows)
        logger.debug(PERSISTENCE_TASK_LISTED, count=len(tasks))
        return tasks

    async def query(
        self,
        filter_spec: TaskFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Task, ...]:
        """Query tasks matching the filter spec.

        Ordering is deterministic on the primary key ``id`` so paginated
        callers see stable windows.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_TASK_LIST_FAILED
        )
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.status is not None:
            clauses.append("status = ?")
            params.append(filter_spec.status.value)
        if filter_spec.assigned_to is not None:
            clauses.append("assigned_to = ?")
            params.append(filter_spec.assigned_to)
        if filter_spec.project is not None:
            clauses.append("project = ?")
            params.append(filter_spec.project)

        query = f"SELECT {self._TASK_COLUMNS} FROM tasks"  # noqa: S608
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        try:
            cursor = await self._db.execute(query, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list tasks"
            logger.warning(
                PERSISTENCE_TASK_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        tasks = tuple(self._row_to_task(row) for row in rows)
        logger.debug(PERSISTENCE_TASK_LISTED, count=len(tasks))
        return tasks

    async def count(self, filter_spec: TaskFilterSpec) -> int:
        """Count tasks matching the given filter spec."""
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.status is not None:
            clauses.append("status = ?")
            params.append(filter_spec.status.value)
        if filter_spec.assigned_to is not None:
            clauses.append("assigned_to = ?")
            params.append(filter_spec.assigned_to)
        if filter_spec.project is not None:
            clauses.append("project = ?")
            params.append(filter_spec.project)

        query = "SELECT COUNT(*) FROM tasks"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        try:
            cursor = await self._db.execute(query, params)
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to count tasks"
            logger.warning(
                PERSISTENCE_TASK_COUNT_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        total = int(row[0]) if row is not None else 0
        logger.debug(PERSISTENCE_TASK_COUNTED, count=total)
        return total

    async def delete(self, task_id: str) -> bool:
        """Delete a task by ID."""
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM tasks WHERE id = ?", (task_id,)
                )
                await self._db.commit()
                deleted = cursor.rowcount > 0
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to delete task {task_id!r}"
                logger.warning(
                    PERSISTENCE_TASK_DELETE_FAILED,
                    task_id=task_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted


class SQLiteCostRecordRepository:
    """SQLite implementation of the CostRecordRepository protocol.

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

    async def append(self, event: CostRecord) -> None:
        """Persist a cost record (append-only per AppendOnlyRepository)."""
        async with self._write_context():
            try:
                data = event.model_dump(mode="json")
                await self._db.execute(
                    """\
INSERT INTO cost_records (
    agent_id, task_id, provider, model, input_tokens,
    output_tokens, cost, currency, timestamp, call_category
) VALUES (
    :agent_id, :task_id, :provider, :model, :input_tokens,
    :output_tokens, :cost, :currency, :timestamp, :call_category
)""",
                    data,
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to save cost record for agent {event.agent_id!r}"
                logger.warning(
                    PERSISTENCE_COST_RECORD_SAVE_FAILED,
                    agent_id=event.agent_id,
                    task_id=event.task_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: CostRecordFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CostRecord, ...]:
        """Query cost records matching filter spec with pagination."""
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(filter_spec.agent_id)
        if filter_spec.task_id is not None:
            clauses.append("task_id = ?")
            params.append(filter_spec.task_id)

        sql = """\
SELECT agent_id, task_id, provider, model, input_tokens,
       output_tokens, cost, currency, timestamp, call_category
FROM cost_records"""
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC, agent_id ASC, rowid ASC"
        effective_offset = max(0, int(offset))
        sql += " LIMIT ? OFFSET ?"
        params.extend([int(limit), effective_offset])

        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
            records = tuple(CostRecord.model_validate(dict(row)) for row in rows)
        except (
            sqlite3.Error,
            aiosqlite.Error,
            json.JSONDecodeError,
            ValidationError,
        ) as exc:
            msg = "Failed to query cost records"
            logger.warning(
                PERSISTENCE_COST_RECORD_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_COST_RECORD_QUERIED, count=len(records))
        return records

    async def aggregate(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> float:
        """Sum total cost, optionally filtered by agent and/or task.

        Raises :class:`MixedCurrencyAggregationError` when the matched rows
        span multiple currencies.  The distinct-currency probe and the
        ``SUM`` run in a **single** aggregating query (``COUNT(DISTINCT)``
        + ``GROUP_CONCAT(DISTINCT)`` + ``SUM``) so the two observations
        share one snapshot and a concurrent insert cannot change the
        result between them.
        """
        try:
            conditions: list[str] = []
            params: list[str] = []
            if agent_id is not None:
                conditions.append("agent_id = ?")
                params.append(agent_id)
            if task_id is not None:
                conditions.append("task_id = ?")
                params.append(task_id)
            where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

            # where_clause is built from fixed column names only; user
            # values go through bound parameters.
            agg_select = (
                "SELECT "
                "COUNT(DISTINCT currency) AS distinct_count, "
                "GROUP_CONCAT(DISTINCT currency) AS currencies, "
                "COALESCE(SUM(cost), 0.0) AS total_cost "
                "FROM cost_records"
            )
            agg_sql = f"{agg_select}{where_clause}"
            cursor = await self._db.execute(agg_sql, tuple(params))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to aggregate cost records"
            logger.warning(
                PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            msg = "aggregate query returned no rows"
            logger.error(
                PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
                agent_id=agent_id,
                error=msg,
            )
            raise QueryError(msg)
        distinct_count = int(row[0] or 0)
        currencies_csv = row[1]
        total = float(row[2])
        if distinct_count > 1:
            distinct = frozenset(c for c in (currencies_csv or "").split(",") if c)
            logger.error(
                PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                currencies=sorted(distinct),
                error="mixed-currency aggregation rejected",
            )
            mixed_msg = "Cannot aggregate costs across mixed currencies"
            raise MixedCurrencyAggregationError(
                mixed_msg,
                currencies=distinct,
                agent_id=agent_id,
                task_id=task_id,
            )
        logger.debug(
            PERSISTENCE_COST_RECORD_AGGREGATED,
            agent_id=agent_id,
            total_cost=total,
        )
        return total

    async def purge_before(self, threshold: datetime) -> int:
        """Delete cost records with timestamp before threshold (retention).

        ``threshold`` must be timezone-aware: a naive value compared
        against UTC-formatted stored timestamps would silently delete
        the wrong window.
        """
        if threshold.tzinfo is None:
            msg = f"threshold must be timezone-aware, got naive {threshold!r}"
            logger.warning(
                PERSISTENCE_COST_RECORD_QUERY_FAILED,
                error="naive_threshold",
                error_type="ValueError",
            )
            raise QueryError(msg)
        aware_threshold = normalize_utc(threshold)
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM cost_records WHERE timestamp < ?",
                    (format_iso_utc(aware_threshold),),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = "Failed to purge cost records by threshold"
                logger.warning(
                    PERSISTENCE_COST_RECORD_QUERY_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return cursor.rowcount


class SQLiteMessageRepository:
    """SQLite implementation of the MessageRepository protocol.

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

    async def _safe_rollback(self, msg_id: str) -> None:
        """Best-effort rollback on the shared aiosqlite connection.

        A secondary rollback failure must not mask the original write
        error, but we DO log it because a tainted shared connection is
        worth a trail in observability. Without this rollback, a failed
        write inside the shared transaction poisons it for every sibling
        repo holding the same ``aiosqlite.Connection``. Mirrors the
        pattern used by the 37 sibling repos in this package.
        """
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                PERSISTENCE_MESSAGE_SAVE_FAILED,
                message_id=msg_id,
                error_type=type(rollback_exc).__name__,
                error=safe_error_description(rollback_exc),
                rollback_failed=True,
            )

    async def append(self, message: Message) -> None:
        """Persist a message (append-only per AppendOnlyRepository)."""
        data = message.model_dump(mode="json")
        msg_id = str(message.id)

        async with self._write_context():
            try:
                await self._db.execute(
                    """\
INSERT INTO messages (
    id, timestamp, sender, "to", type, priority,
    channel, content, attachments, metadata
) VALUES (
    :id, :timestamp, :sender, :to, :type, :priority,
    :channel, :content, :attachments, :metadata
)""",
                    {
                        "id": msg_id,
                        "timestamp": data["timestamp"],
                        "sender": data["sender"],
                        "to": data["to"],
                        "type": data["type"],
                        "priority": data["priority"],
                        "channel": data["channel"],
                        "content": json.dumps(data["parts"]),
                        "attachments": "[]",
                        "metadata": json.dumps(data["metadata"]),
                    },
                )
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await self._safe_rollback(msg_id)
                if is_unique_constraint_error(exc):
                    err_msg = f"Message {msg_id} already exists"
                    logger.warning(PERSISTENCE_MESSAGE_DUPLICATE, message_id=msg_id)
                    raise DuplicateRecordError(err_msg) from exc
                # Other integrity errors (NOT NULL, different UNIQUE).
                msg = f"Failed to save message {msg_id!r}"
                logger.warning(
                    PERSISTENCE_MESSAGE_SAVE_FAILED,
                    message_id=msg_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(msg_id)
                msg = f"Failed to save message {msg_id!r}"
                logger.warning(
                    PERSISTENCE_MESSAGE_SAVE_FAILED,
                    message_id=msg_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    def _row_to_message(self, row: aiosqlite.Row) -> Message:
        """Reconstruct a Message from a database row."""
        try:
            data = dict(row)
            # Map DB column "sender" to Message's "from" alias.
            data["from"] = data.pop("sender")
            # Parts are stored as JSON in the content column.
            data["parts"] = json.loads(data.pop("content"))
            data.pop("attachments", None)
            data["metadata"] = json.loads(data["metadata"])
            return Message.model_validate(data)
        except (
            json.JSONDecodeError,
            ValidationError,
            KeyError,
            TypeError,
        ) as exc:
            msg_id = row["id"] if row else "unknown"
            msg = f"Failed to deserialize message {msg_id!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_DESERIALIZE_FAILED,
                message_id=msg_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get_history(
        self,
        channel: str,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[Message, ...]:
        """Retrieve message history for a channel, newest first."""
        if limit is not None and limit < 1:
            msg = f"limit must be a positive integer, got {limit}"
            raise QueryError(msg)
        sql = """\
SELECT id, timestamp, sender, "to", type, priority,
       channel, content, attachments, metadata
FROM messages
WHERE channel = ?
ORDER BY timestamp DESC"""
        params: list[object] = [channel]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch message history for channel {channel!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_HISTORY_FAILED,
                channel=channel,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        messages = tuple(self._row_to_message(row) for row in rows)
        logger.debug(
            PERSISTENCE_MESSAGE_HISTORY_FETCHED,
            channel=channel,
            count=len(messages),
        )
        return messages

    async def query(
        self,
        filter_spec: MessageFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Message, ...]:
        """Return messages matching the filter spec, newest first."""
        if limit < 1:
            msg = f"limit must be a positive integer, got {limit}"
            raise QueryError(msg)
        sql = """\
SELECT id, timestamp, sender, "to", type, priority,
       channel, content, attachments, metadata
FROM messages"""
        params: list[object] = []
        if filter_spec.channel is not None:
            sql += " WHERE channel = ?"
            params.append(filter_spec.channel)
        sql += " ORDER BY timestamp DESC, id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query messages"
            logger.warning(
                PERSISTENCE_MESSAGE_HISTORY_FAILED,
                channel=filter_spec.channel,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_message(row) for row in rows)

    async def purge_before(self, threshold: datetime) -> int:
        """Delete messages with ``timestamp < threshold`` (retention).

        ``threshold`` must be timezone-aware: a naive value compared
        against UTC-formatted stored timestamps would silently delete
        the wrong window.
        """
        if threshold.tzinfo is None:
            msg = f"threshold must be timezone-aware, got naive {threshold!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_DELETE_FAILED,
                error="naive_threshold",
                error_type="ValueError",
            )
            raise QueryError(msg)
        aware_threshold = normalize_utc(threshold)
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM messages WHERE timestamp < ?",
                    (format_iso_utc(aware_threshold),),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = "Failed to purge messages by threshold"
                logger.warning(
                    PERSISTENCE_MESSAGE_DELETE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return cursor.rowcount

    async def delete(self, message_id: NotBlankStr) -> bool:
        """Delete a single message by id (bespoke per ADR D7, moderation).

        Returns ``True`` when a row was removed, ``False`` when the id
        did not exist. Concurrent writes are serialized through the
        shared backend write context. The audit-grade mutation log is
        emitted by :class:`MessageService.delete_message`; the
        repository never logs mutations itself (persistence-boundary
        rule, see ``docs/reference/persistence-boundary.md``).
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM messages WHERE id = ?",
                    (message_id,),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to delete message {message_id!r}"
                logger.warning(
                    PERSISTENCE_MESSAGE_DELETE_FAILED,
                    message_id=message_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return cursor.rowcount > 0
