# module-kind: repository
"""SQLite repository implementation for Task."""

import json
import sqlite3

import aiosqlite
from pydantic import BaseModel, ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.task import Task
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.task import (
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
from synthorg.persistence.sqlite._shared import WriteContext
from synthorg.persistence.task_protocol import TaskFilterSpec

logger = get_logger(__name__)


def _json_list(items: tuple[object, ...]) -> str:
    """Serialize a tuple of Pydantic models or scalars to a JSON array.

    Items must be JSON-serializable or Pydantic models.
    Non-serializable items will raise ``TypeError``.

    Returns:
        Result of type ``str``.
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

    _UPSERT_SQL = """\
INSERT INTO tasks (
    id, title, description, type, priority, project, created_by,
    requested_by_user_id, assigned_to, status, estimated_complexity,
    budget_limit, deadline, max_retries, parent_task_id, task_structure,
    coordination_topology, reviewers, dependencies, artifacts_expected,
    acceptance_criteria, delegation_chain
) VALUES (
    :id, :title, :description, :type, :priority, :project, :created_by,
    :requested_by_user_id, :assigned_to, :status, :estimated_complexity,
    :budget_limit, :deadline, :max_retries, :parent_task_id, :task_structure,
    :coordination_topology, :reviewers, :dependencies, :artifacts_expected,
    :acceptance_criteria, :delegation_chain
)
ON CONFLICT(id) DO UPDATE SET
    title=excluded.title,
    description=excluded.description,
    type=excluded.type,
    priority=excluded.priority,
    project=excluded.project,
    created_by=excluded.created_by,
    requested_by_user_id=excluded.requested_by_user_id,
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
"""

    @staticmethod
    def _upsert_params(task: Task) -> dict[str, object]:
        """Build the named-parameter dict for one task upsert.

        Returns:
            The ``model_dump`` payload with tuple fields JSON-encoded.
        """
        params = task.model_dump(mode="json")
        # Tuple fields must be stored as JSON strings.
        params["reviewers"] = _json_list(task.reviewers)
        params["dependencies"] = _json_list(task.dependencies)
        params["artifacts_expected"] = _json_list(task.artifacts_expected)
        params["acceptance_criteria"] = _json_list(task.acceptance_criteria)
        params["delegation_chain"] = _json_list(task.delegation_chain)
        return params

    async def save(self, task: Task) -> None:
        """Persist a task (upsert semantics).

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(self._UPSERT_SQL, self._upsert_params(task))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to save task {task.id!r}"
                logger.warning(
                    PERSISTENCE_TASK_SAVE_FAILED,
                    task_id=str(task.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def save_many(self, tasks: tuple[Task, ...]) -> None:
        """Upsert many tasks in one transaction (ADR-0001 D7).

        Raises:
            QueryError: If the database query fails.
        """
        if not tasks:
            return
        async with self._write_context():
            try:
                await self._db.executemany(
                    self._UPSERT_SQL,
                    [self._upsert_params(task) for task in tasks],
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to save {len(tasks)} tasks"
                logger.warning(
                    PERSISTENCE_TASK_SAVE_FAILED,
                    task_count=len(tasks),
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
        """Reconstruct a Task from a database row.

        Returns:
            Result of type ``Task``.

        Raises:
            QueryError: If the database query fails.
        """
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
       requested_by_user_id, assigned_to, status, estimated_complexity,
       budget_limit, deadline, max_retries, parent_task_id, task_structure,
       coordination_topology, reviewers, dependencies, artifacts_expected,
       acceptance_criteria, delegation_chain"""

    async def get(self, task_id: str) -> Task | None:
        """Retrieve a task by its ID.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                f"SELECT {self._TASK_COLUMNS} FROM tasks WHERE id = ?",  # noqa: S608
                (task_id,),
            ) as cursor:
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

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_TASK_LIST_FAILED
        )
        query = (
            f"SELECT {self._TASK_COLUMNS} FROM tasks ORDER BY id ASC LIMIT ? OFFSET ?"  # noqa: S608
        )
        params: list[object] = [limit, offset]

        try:
            async with self._db.execute(query, params) as cursor:
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

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
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
            async with self._db.execute(query, params) as cursor:
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
        """Count tasks matching the given filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
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
            async with self._db.execute(query, params) as cursor:
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
        """Delete a task by ID.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM tasks WHERE id = ?", (task_id,)
                ) as cursor:
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
