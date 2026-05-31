# module-kind: repository
"""Postgres repository implementation for Task."""

from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.task import Task
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
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
from synthorg.persistence._shared import validate_pagination_args

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

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
