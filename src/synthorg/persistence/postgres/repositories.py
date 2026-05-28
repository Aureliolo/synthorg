"""Postgres repository implementations for Task, CostRecord, and Message.

HR-related repositories (LifecycleEvent, TaskMetric, CollaborationMetric)
are in ``hr_repositories.py`` within this package.
"""

import json
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.communication.message import Message
from synthorg.core.normalization import parse_comma_list
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
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
    PERSISTENCE_MESSAGE_FETCH_FAILED,
    PERSISTENCE_MESSAGE_FETCHED,
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
    normalize_utc,
    safe_float,
    safe_int,
    validate_pagination_args,
)

if TYPE_CHECKING:
    from datetime import datetime

    from psycopg_pool import AsyncConnectionPool

    from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec
    from synthorg.persistence.message_protocol import MessageFilterSpec
    from synthorg.persistence.task_protocol import TaskFilterSpec

logger = get_logger(__name__)


def _enum_value(value: Any) -> Any:
    """Return ``value.value`` if present, else the value itself.

    Returns:
        Result of type ``Any``.
    """
    return value.value if hasattr(value, "value") else value


def _task_params(task: Task) -> dict[str, Any]:
    """Build the named-parameter dict for a Task insert/upsert.

    JSON-shaped fields are wrapped in ``Jsonb`` so psycopg adapts
    them to the JSONB wire format; datetime and scalar fields pass
    through as native Python objects.

    Returns:
        Result of type ``dict[str, Any]``.
    """
    dumped = task.model_dump(mode="json")
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "type": _enum_value(task.type),
        "priority": _enum_value(task.priority),
        "project": task.project,
        "created_by": task.created_by,
        "assigned_to": task.assigned_to,
        "status": _enum_value(task.status),
        "estimated_complexity": _enum_value(task.estimated_complexity),
        "budget_limit": task.budget_limit,
        "deadline": task.deadline,
        "max_retries": task.max_retries,
        "parent_task_id": task.parent_task_id,
        # task_structure is stored as JSONB in Postgres (TEXT in SQLite);
        # wrap the serialized scalar so psycopg emits valid JSONB.
        "task_structure": Jsonb(dumped["task_structure"])
        if task.task_structure is not None
        else None,
        "coordination_topology": _enum_value(task.coordination_topology),
        "reviewers": Jsonb(dumped["reviewers"]),
        "dependencies": Jsonb(dumped["dependencies"]),
        "artifacts_expected": Jsonb(dumped["artifacts_expected"]),
        "acceptance_criteria": Jsonb(dumped["acceptance_criteria"]),
        "delegation_chain": Jsonb(dumped["delegation_chain"]),
    }


class PostgresTaskRepository:
    """Postgres implementation of the TaskRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, task: Task) -> None:
        """Persist a task (upsert semantics).

        Raises:
            QueryError: If the database query fails.
        """
        params = _task_params(task)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO tasks (
                        id, title, description, type, priority, project, created_by,
                        assigned_to, status, estimated_complexity, budget_limit,
                        deadline, max_retries, parent_task_id, task_structure,
                        coordination_topology, reviewers, dependencies,
                        artifacts_expected, acceptance_criteria, delegation_chain
                    ) VALUES (
                        %(id)s, %(title)s, %(description)s, %(type)s, %(priority)s,
                        %(project)s, %(created_by)s, %(assigned_to)s, %(status)s,
                        %(estimated_complexity)s, %(budget_limit)s, %(deadline)s,
                        %(max_retries)s, %(parent_task_id)s, %(task_structure)s,
                        %(coordination_topology)s, %(reviewers)s, %(dependencies)s,
                        %(artifacts_expected)s, %(acceptance_criteria)s,
                        %(delegation_chain)s
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        title=EXCLUDED.title,
                        description=EXCLUDED.description,
                        type=EXCLUDED.type,
                        priority=EXCLUDED.priority,
                        project=EXCLUDED.project,
                        created_by=EXCLUDED.created_by,
                        assigned_to=EXCLUDED.assigned_to,
                        status=EXCLUDED.status,
                        estimated_complexity=EXCLUDED.estimated_complexity,
                        budget_limit=EXCLUDED.budget_limit,
                        deadline=EXCLUDED.deadline,
                        max_retries=EXCLUDED.max_retries,
                        parent_task_id=EXCLUDED.parent_task_id,
                        task_structure=EXCLUDED.task_structure,
                        coordination_topology=EXCLUDED.coordination_topology,
                        reviewers=EXCLUDED.reviewers,
                        dependencies=EXCLUDED.dependencies,
                        artifacts_expected=EXCLUDED.artifacts_expected,
                        acceptance_criteria=EXCLUDED.acceptance_criteria,
                        delegation_chain=EXCLUDED.delegation_chain
                    """,
                    params,
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save task {task.id!r}"
            logger.warning(
                PERSISTENCE_TASK_SAVE_FAILED,
                task_id=task.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    _TASK_COLUMNS = (
        "id, title, description, type, priority, project, created_by, "
        "assigned_to, status, estimated_complexity, budget_limit, deadline, "
        "max_retries, parent_task_id, task_structure, coordination_topology, "
        "reviewers, dependencies, artifacts_expected, acceptance_criteria, "
        "delegation_chain"
    )

    def _row_to_task(self, row: dict[str, Any]) -> Task:
        """Reconstruct a Task from a Postgres dict_row.

        Postgres returns JSONB columns as Python lists/dicts and
        TIMESTAMPTZ as timezone-aware datetime, so there is no
        ``json.loads`` step.  The only conversion left is the
        Pydantic round-trip via ``model_validate``.

        Returns:
            Result of type ``Task``.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            data = dict(row)
            return Task.model_validate(data)
        except (ValidationError, KeyError, TypeError) as exc:
            task_id = row.get("id", "unknown")
            msg = f"Failed to deserialize task {task_id!r}"
            logger.warning(
                PERSISTENCE_TASK_DESERIALIZE_FAILED,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, task_id: str) -> Task | None:
        """Retrieve a task by its ID.

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
                await cur.execute(
                    f"SELECT {self._TASK_COLUMNS} FROM tasks WHERE id = %s",  # noqa: S608
                    (task_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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

        Raises:
            QueryError: If the query fails or pagination is out of range.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_TASK_LIST_FAILED
        )
        query = (
            f"SELECT {self._TASK_COLUMNS} FROM tasks ORDER BY id ASC LIMIT %s OFFSET %s"  # noqa: S608
        )
        params: list[object] = [limit, offset]

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(query, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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

        Raises:
            QueryError: If the query fails or pagination is out of range.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_TASK_LIST_FAILED
        )
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.status is not None:
            clauses.append("status = %s")
            params.append(filter_spec.status.value)
        if filter_spec.assigned_to is not None:
            clauses.append("assigned_to = %s")
            params.append(filter_spec.assigned_to)
        if filter_spec.project is not None:
            clauses.append("project = %s")
            params.append(filter_spec.project)

        query = f"SELECT {self._TASK_COLUMNS} FROM tasks"  # noqa: S608
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id ASC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(query, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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
        """Count tasks matching the given filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.status is not None:
            clauses.append("status = %s")
            params.append(filter_spec.status.value)
        if filter_spec.assigned_to is not None:
            clauses.append("assigned_to = %s")
            params.append(filter_spec.assigned_to)
        if filter_spec.project is not None:
            clauses.append("project = %s")
            params.append(filter_spec.project)

        query = "SELECT COUNT(*) AS c FROM tasks"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(query, params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to count tasks"
            logger.warning(
                PERSISTENCE_TASK_COUNT_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        total = int(row["c"]) if row is not None else 0
        logger.debug(PERSISTENCE_TASK_COUNTED, count=total)
        return total

    async def delete(self, task_id: str) -> bool:
        """Delete a task by ID.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete task {task_id!r}"
            logger.warning(
                PERSISTENCE_TASK_DELETE_FAILED,
                task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted


class PostgresCostRecordRepository:
    """Postgres implementation of the CostRecordRepository protocol.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, event: CostRecord) -> None:
        """Persist a cost record (append-only per AppendOnlyRepository).

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO cost_records (
                        agent_id, task_id, provider, model, input_tokens,
                        output_tokens, cost, currency, timestamp,
                        call_category
                    ) VALUES (
                        %(agent_id)s, %(task_id)s, %(provider)s, %(model)s,
                        %(input_tokens)s, %(output_tokens)s, %(cost)s,
                        %(currency)s, %(timestamp)s, %(call_category)s
                    )
                    """,
                    {
                        "agent_id": event.agent_id,
                        "task_id": event.task_id,
                        "provider": event.provider,
                        "model": event.model,
                        "input_tokens": event.input_tokens,
                        "output_tokens": event.output_tokens,
                        "cost": event.cost,
                        "currency": event.currency,
                        "timestamp": event.timestamp,
                        "call_category": event.call_category,
                    },
                )
                await conn.commit()
        except psycopg.Error as exc:
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
        """Query cost records matching filter spec with pagination.

        Raises:
            QueryError: If the query fails or pagination is out of range.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_COST_RECORD_QUERY_FAILED
        )
        clauses: list[str] = []
        params: list[object] = []
        if filter_spec.agent_id is not None:
            clauses.append("agent_id = %s")
            params.append(filter_spec.agent_id)
        if filter_spec.task_id is not None:
            clauses.append("task_id = %s")
            params.append(filter_spec.task_id)

        sql = (
            "SELECT agent_id, task_id, provider, model, input_tokens, "
            "output_tokens, cost, currency, timestamp, call_category "
            "FROM cost_records"
        )
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC, agent_id ASC, rowid ASC"
        sql += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query cost records"
            logger.warning(
                PERSISTENCE_COST_RECORD_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            records = tuple(CostRecord.model_validate(row) for row in rows)
        except ValidationError as exc:
            msg = "Failed to deserialize cost records"
            logger.warning(
                PERSISTENCE_COST_RECORD_QUERY_FAILED,
                error_type="ValidationError",
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
        ``SUM`` run in a **single** aggregating query
        (``COUNT(DISTINCT)`` + ``STRING_AGG(DISTINCT)`` + ``SUM``) so the
        two observations share one snapshot and a concurrent commit
        cannot change the result between them.

        Returns:
            Result of type ``float``.

        Raises:
            QueryError: If the database query fails.
            MixedCurrencyAggregationError: If aggregated rows mix currencies.
        """
        conditions: list[str] = []
        params: list[str] = []
        if agent_id is not None:
            conditions.append("agent_id = %s")
            params.append(agent_id)
        if task_id is not None:
            conditions.append("task_id = %s")
            params.append(task_id)
        where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        # where_clause is built from fixed column names only; user values
        # go through bound %s parameters.
        agg_select = (
            "SELECT "
            "COUNT(DISTINCT currency) AS distinct_count, "
            "STRING_AGG(DISTINCT currency, ',') AS currencies, "
            "COALESCE(SUM(cost), 0.0) AS total_cost "
            "FROM cost_records"
        )
        agg_sql = f"{agg_select}{where_clause}"

        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(agg_sql, params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        distinct_count = safe_int(row[0], default=0)
        currencies_csv = row[1]
        total = safe_float(row[2], default=0.0)
        if distinct_count > 1:
            distinct = frozenset(parse_comma_list(currencies_csv))
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

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM cost_records WHERE timestamp < %s",
                    (normalize_utc(threshold),),
                )
                deleted_count = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge cost records by threshold"
            logger.warning(
                PERSISTENCE_COST_RECORD_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted_count


class PostgresMessageRepository:
    """Postgres implementation of the MessageRepository protocol.

    ``content`` is stored as TEXT containing a JSON-serialized ``parts``
    array (same as SQLite, for protocol compatibility).  ``metadata``
    and ``attachments`` use native JSONB.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, message: Message) -> None:
        """Persist a message (append-only per AppendOnlyRepository).

        Raises:
            DuplicateRecordError: If a row with the same key already exists.
            QueryError: If the database query fails.
        """
        data = message.model_dump(mode="json")
        msg_id = str(message.id)

        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO messages (
                        id, timestamp, sender, "to", type, priority,
                        channel, content, attachments, metadata
                    ) VALUES (
                        %(id)s, %(timestamp)s, %(sender)s, %(to)s, %(type)s,
                        %(priority)s, %(channel)s, %(content)s, %(attachments)s,
                        %(metadata)s
                    )
                    """,
                    {
                        "id": msg_id,
                        "timestamp": message.timestamp,
                        "sender": data["sender"],
                        "to": data["to"],
                        "type": data["type"],
                        "priority": data["priority"],
                        "channel": data["channel"],
                        "content": json.dumps(data["parts"]),
                        "attachments": Jsonb(data.get("attachments", [])),
                        "metadata": Jsonb(data["metadata"]),
                    },
                )
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            err_msg = f"Message {msg_id} already exists"
            logger.warning(PERSISTENCE_MESSAGE_DUPLICATE, message_id=msg_id)
            raise DuplicateRecordError(err_msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save message {msg_id!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_SAVE_FAILED,
                message_id=msg_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    def _row_to_message(self, row: dict[str, Any]) -> Message:
        """Reconstruct a Message from a Postgres dict_row.

        Returns:
            Result of type ``Message``.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            data = dict(row)
            # Map DB column "sender" to Message's "from" alias.
            data["from"] = data.pop("sender")
            # Parts are stored as JSON in the content column.
            content = data.pop("content")
            data["parts"] = json.loads(content) if isinstance(content, str) else content
            # attachments round-trips through JSONB as a Python list;
            # leave it in place for Pydantic to validate.
            # metadata comes back as a Python dict (JSONB).
            return Message.model_validate(data)
        except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
            msg_id = row.get("id", "unknown")
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
        """Retrieve message history for a channel, newest first.

        Returns:
            Tuple of matching rows; empty when no rows match.

        Raises:
            QueryError: If the database query fails.
        """
        if limit is not None and (
            not isinstance(limit, int) or isinstance(limit, bool) or limit < 1
        ):
            msg = f"limit must be a positive integer, got {limit!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_HISTORY_FAILED,
                channel=channel,
                error=msg,
            )
            raise QueryError(msg)
        sql = (
            'SELECT id, timestamp, sender, "to", type, priority, '
            "channel, content, attachments, metadata "
            "FROM messages "
            "WHERE channel = %s "
            "ORDER BY timestamp DESC"
        )
        params: list[object] = [channel]
        if limit is not None:
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

    async def get_by_id(
        self,
        channel: str,
        message_id: str,
    ) -> Message | None:
        """Fetch one message by ``(channel, id)`` via the PK point read.

        The ``id`` predicate alone resolves the row (it is the primary
        key); the extra ``channel`` predicate is a deliberate scoping
        guard so a caller holding only a message id cannot read a
        message outside the channel it asked for.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            'SELECT id, timestamp, sender, "to", type, priority, '
            "channel, content, attachments, metadata "
            "FROM messages "
            "WHERE id = %s AND channel = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, [message_id, channel])
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch message {message_id!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_FETCH_FAILED,
                channel=channel,
                message_id=message_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        message = self._row_to_message(row)
        logger.debug(
            PERSISTENCE_MESSAGE_FETCHED,
            channel=channel,
            message_id=message_id,
        )
        return message

    async def query(
        self,
        filter_spec: MessageFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Message, ...]:
        """Return messages matching the filter spec, newest first.

        Raises:
            QueryError: If the query fails or pagination is out of range.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_MESSAGE_HISTORY_FAILED
        )
        sql = (
            'SELECT id, timestamp, sender, "to", type, priority, '
            "channel, content, attachments, metadata "
            "FROM messages"
        )
        params: list[object] = []
        if filter_spec.channel is not None:
            sql += " WHERE channel = %s"
            params.append(filter_spec.channel)
        sql += " ORDER BY timestamp DESC, id ASC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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

        Returns:
            Numeric result of the operation.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM messages WHERE timestamp < %s",
                    (normalize_utc(threshold),),
                )
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge messages by threshold"
            logger.warning(
                PERSISTENCE_MESSAGE_DELETE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount

    async def delete(self, message_id: NotBlankStr) -> bool:
        """Delete a single message by id (bespoke per ADR D7, moderation).

        Returns ``True`` when a row was removed, ``False`` when the id
        did not exist. The audit-grade mutation log is emitted by
        :class:`MessageService.delete_message`; the repository never
        logs mutations itself (persistence-boundary rule, see
        ``docs/reference/persistence-boundary.md``).

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM messages WHERE id = %s",
                    (message_id,),
                )
                await conn.commit()
                deleted = cur.rowcount > 0
        except psycopg.Error as exc:
            msg = f"Failed to delete message {message_id!r}"
            logger.warning(
                PERSISTENCE_MESSAGE_DELETE_FAILED,
                message_id=message_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted
