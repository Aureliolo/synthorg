"""SQLite repository implementations for Task, CostRecord, and Message.

HR-related repositories (LifecycleEvent, TaskMetric, CollaborationMetric)
are in ``hr_repositories.py`` within this package.
"""

import json
import sqlite3

import aiosqlite
from pydantic import BaseModel, ValidationError

from synthorg.budget.cost_record import CostRecord
from synthorg.communication.message import Message
from synthorg.core.enums import TaskStatus  # noqa: TC001
from synthorg.core.task import Task
from synthorg.observability import get_logger
from synthorg.observability.events.persistence import (
    PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
    PERSISTENCE_COST_RECORD_AGGREGATED,
    PERSISTENCE_COST_RECORD_QUERIED,
    PERSISTENCE_COST_RECORD_QUERY_FAILED,
    PERSISTENCE_COST_RECORD_SAVE_FAILED,
    PERSISTENCE_COST_RECORD_SAVED,
    PERSISTENCE_MESSAGE_DESERIALIZE_FAILED,
    PERSISTENCE_MESSAGE_DUPLICATE,
    PERSISTENCE_MESSAGE_HISTORY_FAILED,
    PERSISTENCE_MESSAGE_HISTORY_FETCHED,
    PERSISTENCE_MESSAGE_SAVE_FAILED,
    PERSISTENCE_MESSAGE_SAVED,
    PERSISTENCE_TASK_DELETE_FAILED,
    PERSISTENCE_TASK_DELETED,
    PERSISTENCE_TASK_DESERIALIZE_FAILED,
    PERSISTENCE_TASK_FETCH_FAILED,
    PERSISTENCE_TASK_FETCHED,
    PERSISTENCE_TASK_LIST_FAILED,
    PERSISTENCE_TASK_LISTED,
    PERSISTENCE_TASK_SAVE_FAILED,
    PERSISTENCE_TASK_SAVED,
)
from synthorg.persistence.errors import DuplicateRecordError, QueryError

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
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, task: Task) -> None:
        """Persist a task (upsert semantics)."""
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
            logger.exception(
                PERSISTENCE_TASK_SAVE_FAILED, task_id=task.id, error=str(exc)
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_TASK_SAVED, task_id=task.id)

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
            logger.exception(
                PERSISTENCE_TASK_DESERIALIZE_FAILED,
                task_id=task_id,
                error=str(exc),
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
            logger.exception(
                PERSISTENCE_TASK_FETCH_FAILED,
                task_id=task_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(PERSISTENCE_TASK_FETCHED, task_id=task_id, found=False)
            return None
        logger.debug(PERSISTENCE_TASK_FETCHED, task_id=task_id, found=True)
        return self._row_to_task(row)

    async def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        assigned_to: str | None = None,
        project: str | None = None,
    ) -> tuple[Task, ...]:
        """List tasks with optional filters."""
        clauses: list[str] = []
        params: list[str] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if assigned_to is not None:
            clauses.append("assigned_to = ?")
            params.append(assigned_to)
        if project is not None:
            clauses.append("project = ?")
            params.append(project)

        query = f"SELECT {self._TASK_COLUMNS} FROM tasks"  # noqa: S608
        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        try:
            cursor = await self._db.execute(query, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list tasks"
            logger.exception(PERSISTENCE_TASK_LIST_FAILED, error=str(exc))
            raise QueryError(msg) from exc
        tasks = tuple(self._row_to_task(row) for row in rows)
        logger.debug(PERSISTENCE_TASK_LISTED, count=len(tasks))
        return tasks

    async def delete(self, task_id: str) -> bool:
        """Delete a task by ID."""
        try:
            cursor = await self._db.execute(
                "DELETE FROM tasks WHERE id = ?", (task_id,)
            )
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to delete task {task_id!r}"
            logger.exception(
                PERSISTENCE_TASK_DELETE_FAILED,
                task_id=task_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        deleted = cursor.rowcount > 0
        logger.debug(PERSISTENCE_TASK_DELETED, task_id=task_id, deleted=deleted)
        return deleted


class SQLiteCostRecordRepository:
    """SQLite implementation of the CostRecordRepository protocol.

    Args:
        db: An open aiosqlite connection.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, record: CostRecord) -> None:
        """Persist a cost record (append-only)."""
        try:
            data = record.model_dump(mode="json")
            await self._db.execute(
                """\
INSERT INTO cost_records (
    agent_id, task_id, provider, model, input_tokens,
    output_tokens, cost_usd, timestamp, call_category
) VALUES (
    :agent_id, :task_id, :provider, :model, :input_tokens,
    :output_tokens, :cost_usd, :timestamp, :call_category
)""",
                data,
            )
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to save cost record for agent {record.agent_id!r}"
            logger.exception(
                PERSISTENCE_COST_RECORD_SAVE_FAILED,
                agent_id=record.agent_id,
                task_id=record.task_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_COST_RECORD_SAVED,
            agent_id=record.agent_id,
            task_id=record.task_id,
        )

    async def query(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> tuple[CostRecord, ...]:
        """Query cost records with optional filters."""
        clauses: list[str] = []
        params: list[str] = []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)

        sql = """\
SELECT agent_id, task_id, provider, model, input_tokens,
       output_tokens, cost_usd, timestamp, call_category
FROM cost_records"""
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)

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
            logger.exception(PERSISTENCE_COST_RECORD_QUERY_FAILED, error=str(exc))
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_COST_RECORD_QUERIED, count=len(records))
        return records

    async def aggregate(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> float:
        """Sum total cost_usd, optionally filtered by agent and/or task."""
        try:
            sql = "SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_records"
            conditions: list[str] = []
            params: list[str] = []
            if agent_id is not None:
                conditions.append("agent_id = ?")
                params.append(agent_id)
            if task_id is not None:
                conditions.append("task_id = ?")
                params.append(task_id)
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
            cursor = await self._db.execute(sql, tuple(params))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to aggregate cost records"
            logger.exception(
                PERSISTENCE_COST_RECORD_AGGREGATE_FAILED,
                agent_id=agent_id,
                error=str(exc),
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
        total = float(row[0])
        logger.debug(
            PERSISTENCE_COST_RECORD_AGGREGATED,
            agent_id=agent_id,
            total_usd=total,
        )
        return total


class SQLiteMessageRepository:
    """SQLite implementation of the MessageRepository protocol.

    Args:
        db: An open aiosqlite connection.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, message: Message) -> None:
        """Persist a message."""
        data = message.model_dump(mode="json")
        msg_id = str(message.id)

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
                    "content": data["content"],
                    "attachments": json.dumps(data["attachments"]),
                    "metadata": json.dumps(data["metadata"]),
                },
            )
            await self._db.commit()
        except sqlite3.IntegrityError as exc:
            error_text = str(exc)
            is_duplicate_id = (
                "UNIQUE constraint failed: messages.id" in error_text
                or "PRIMARY KEY" in error_text
            )
            if is_duplicate_id:
                err_msg = f"Message {msg_id} already exists"
                logger.warning(PERSISTENCE_MESSAGE_DUPLICATE, message_id=msg_id)
                raise DuplicateRecordError(err_msg) from exc
            # Other integrity errors (NOT NULL, different UNIQUE).
            msg = f"Failed to save message {msg_id!r}"
            logger.exception(
                PERSISTENCE_MESSAGE_SAVE_FAILED,
                message_id=msg_id,
                error=error_text,
            )
            raise QueryError(msg) from exc
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to save message {msg_id!r}"
            logger.exception(
                PERSISTENCE_MESSAGE_SAVE_FAILED,
                message_id=msg_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_MESSAGE_SAVED, message_id=msg_id)

    def _row_to_message(self, row: aiosqlite.Row) -> Message:
        """Reconstruct a Message from a database row."""
        try:
            data = dict(row)
            # Map DB column "sender" to Message's "from" alias.
            data["from"] = data.pop("sender")
            data["attachments"] = json.loads(data["attachments"])
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
            logger.exception(
                PERSISTENCE_MESSAGE_DESERIALIZE_FAILED,
                message_id=msg_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc

    async def get_history(
        self,
        channel: str,
        *,
        limit: int | None = None,
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
            logger.exception(
                PERSISTENCE_MESSAGE_HISTORY_FAILED,
                channel=channel,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        messages = tuple(self._row_to_message(row) for row in rows)
        logger.debug(
            PERSISTENCE_MESSAGE_HISTORY_FETCHED,
            channel=channel,
            count=len(messages),
        )
        return messages
