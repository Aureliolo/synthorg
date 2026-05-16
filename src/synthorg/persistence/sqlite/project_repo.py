"""SQLite repository implementation for Project."""

import json
import sqlite3

import aiosqlite
from pydantic import ValidationError

from synthorg.core.enums import ProjectStatus
from synthorg.core.persistence_errors import (
    DuplicateRecordError,
    QueryError,
    RecordNotFoundError,
)
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_PROJECT_DELETE_FAILED,
    PERSISTENCE_PROJECT_DESERIALIZE_FAILED,
    PERSISTENCE_PROJECT_FETCH_FAILED,
    PERSISTENCE_PROJECT_FETCHED,
    PERSISTENCE_PROJECT_LIST_FAILED,
    PERSISTENCE_PROJECT_LISTED,
    PERSISTENCE_PROJECT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.project_protocol import ProjectFilterSpec
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


def _row_to_project(row: aiosqlite.Row) -> Project:
    """Reconstruct a ``Project`` from a database row.

    Args:
        row: A single database row with project columns.

    Returns:
        Validated ``Project`` model instance.
    """
    data = dict(row)
    data["status"] = ProjectStatus(data["status"])
    data["team"] = tuple(json.loads(data["team"]))
    data["task_ids"] = tuple(json.loads(data["task_ids"]))
    return Project.model_validate(data)


class SQLiteProjectRepository:
    """SQLite-backed project repository.

    Provides CRUD operations for ``Project`` models using a shared
    ``aiosqlite.Connection``.  All write operations commit immediately.

    Args:
        db: An open aiosqlite connection with ``row_factory``
            set to ``aiosqlite.Row``.
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

    @staticmethod
    def _row_params(project: Project) -> tuple[object, ...]:
        return (
            project.id,
            project.name,
            project.description,
            json.dumps(list(project.team)),
            project.lead,
            json.dumps(list(project.task_ids)),
            project.deadline,
            project.budget,
            project.status.value,
        )

    async def create(self, project: Project) -> None:
        """Insert a new project, failing if the id already exists.

        Args:
            project: Project model to insert.

        Raises:
            DuplicateRecordError: A project with the same id exists.
            QueryError: If the database operation fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    """\
INSERT INTO projects (id, name, description, team, lead,
                      task_ids, deadline, budget, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    self._row_params(project),
                )
                await self._db.commit()
            except (sqlite3.IntegrityError, aiosqlite.IntegrityError) as exc:
                await self._safe_rollback()
                logger.warning(
                    PERSISTENCE_PROJECT_SAVE_FAILED,
                    project_id=project.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    sqlite_errorname=getattr(exc, "sqlite_errorname", None),
                )
                if is_unique_constraint_error(exc):
                    msg = f"Project with id {project.id!r} already exists"
                    raise DuplicateRecordError(msg) from exc
                # NOT NULL / CHECK / FOREIGN KEY / TRIGGER: real
                # invariant violation, not a lifecycle conflict.
                msg = f"Failed to create project {project.id!r}"
                raise QueryError(msg) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = f"Failed to create project {project.id!r}"
                logger.warning(
                    PERSISTENCE_PROJECT_SAVE_FAILED,
                    project_id=project.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def _safe_rollback(self) -> None:
        """Best-effort rollback on the shared connection.

        ``SQLiteProjectRepository`` shares a single
        :class:`aiosqlite.Connection` across requests.  When
        ``execute()`` / ``commit()`` raises, leaving the transaction
        open lets later calls inherit the failed state or keep the DB
        locked.  Rolling back here clears the per-write transaction
        without disturbing successful sibling repositories that share
        the same connection.

        The rollback itself is wrapped: a secondary failure (e.g. the
        connection is already closed) must not mask the original error
        the caller is propagating.  We DO log the rollback failure
        though, so a tainted shared connection leaves a trail in
        observability instead of silently degrading later writes.
        """
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                PERSISTENCE_PROJECT_SAVE_FAILED,
                error_type=type(rollback_exc).__name__,
                error=safe_error_description(rollback_exc),
                rollback_failed=True,
            )

    async def update(self, project: Project) -> None:
        """Update an existing project, failing if no row matched.

        Args:
            project: Project model to update.  ``project.id`` selects
                the row.

        Raises:
            RecordNotFoundError: No project with this id exists.
            QueryError: If the database operation fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    """\
UPDATE projects SET
    name=?,
    description=?,
    team=?,
    lead=?,
    task_ids=?,
    deadline=?,
    budget=?,
    status=?
WHERE id=?""",
                    (
                        project.name,
                        project.description,
                        json.dumps(list(project.team)),
                        project.lead,
                        json.dumps(list(project.task_ids)),
                        project.deadline,
                        project.budget,
                        project.status.value,
                        project.id,
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = f"Failed to update project {project.id!r}"
                logger.warning(
                    PERSISTENCE_PROJECT_SAVE_FAILED,
                    project_id=project.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            if cursor.rowcount == 0:
                logger.warning(
                    PERSISTENCE_PROJECT_SAVE_FAILED,
                    project_id=project.id,
                    error_type="RecordNotFoundError",
                    error="No project with matching id",
                )
                msg = f"No project with id {project.id!r}"
                raise RecordNotFoundError(msg)

    async def save(self, project: Project) -> None:
        """Persist a project via upsert (migration / import paths).

        Args:
            project: Project model to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    """\
INSERT INTO projects (id, name, description, team, lead,
                      task_ids, deadline, budget, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    name=excluded.name,
    description=excluded.description,
    team=excluded.team,
    lead=excluded.lead,
    task_ids=excluded.task_ids,
    deadline=excluded.deadline,
    budget=excluded.budget,
    status=excluded.status""",
                    self._row_params(project),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = f"Failed to save project {project.id!r}"
                logger.warning(
                    PERSISTENCE_PROJECT_SAVE_FAILED,
                    project_id=project.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, project_id: NotBlankStr) -> Project | None:
        """Retrieve a project by primary key.

        Args:
            project_id: Unique project identifier.

        Returns:
            The matching ``Project``, or ``None`` if not found.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        try:
            cursor = await self._db.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch project {project_id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_FETCH_FAILED,
                project_id=project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(
                PERSISTENCE_PROJECT_FETCHED, project_id=project_id, found=False
            )
            return None
        try:
            project = _row_to_project(row)
        except (ValueError, ValidationError, json.JSONDecodeError, KeyError) as exc:
            msg = f"Failed to deserialize project {project_id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_DESERIALIZE_FAILED,
                project_id=project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_PROJECT_FETCHED, project_id=project_id, found=True)
        return project

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Project, ...]:
        """List all projects in ID order.

        Args:
            limit: Maximum projects to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Projects in ascending ID order.

        Raises:
            QueryError: If the database query, deserialization, or
                pagination validation fails.
        """
        return await self.query(
            ProjectFilterSpec(),
            limit=limit,
            offset=offset,
        )

    async def query(
        self,
        filter_spec: ProjectFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Project, ...]:
        """List projects matching the filter spec.

        Args:
            filter_spec: Carries optional ``status`` and ``lead`` filters.
            limit: Maximum projects to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Matching projects ordered by ID.

        Raises:
            QueryError: If the database query, deserialization, or
                pagination validation fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PROJECT_LIST_FAILED
        )
        # Clamp under the hard ceiling so a caller-supplied limit
        # cannot exceed the table-scan safety bound.
        effective_limit = min(limit, _MAX_LIST_ROWS)
        query = "SELECT * FROM projects"
        conditions: list[str] = []
        params: list[object] = []

        if filter_spec.status is not None:
            conditions.append("status = ?")
            params.append(filter_spec.status.value)
        if filter_spec.lead is not None:
            conditions.append("lead = ?")
            params.append(filter_spec.lead)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id LIMIT ? OFFSET ?"
        params.append(effective_limit)
        params.append(offset)

        try:
            cursor = await self._db.execute(query, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list projects"
            logger.warning(
                PERSISTENCE_PROJECT_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            projects = tuple(_row_to_project(row) for row in rows)
        except (ValueError, ValidationError, json.JSONDecodeError, KeyError) as exc:
            msg = "Failed to deserialize projects"
            logger.warning(
                PERSISTENCE_PROJECT_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_PROJECT_LISTED, count=len(projects))
        return projects

    async def count(self, filter_spec: ProjectFilterSpec) -> int:
        """Count projects matching the filter spec."""
        query = "SELECT COUNT(*) FROM projects"
        conditions: list[str] = []
        params: list[object] = []

        if filter_spec.status is not None:
            conditions.append("status = ?")
            params.append(filter_spec.status.value)
        if filter_spec.lead is not None:
            conditions.append("lead = ?")
            params.append(filter_spec.lead)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        try:
            cursor = await self._db.execute(query, params)
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to count projects"
            logger.warning(
                PERSISTENCE_PROJECT_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row[0]) if row else 0

    async def delete(self, project_id: NotBlankStr) -> bool:
        """Delete a project by primary key.

        Args:
            project_id: Unique project identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM projects WHERE id = ?", (project_id,)
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = f"Failed to delete project {project_id!r}"
                logger.warning(
                    PERSISTENCE_PROJECT_DELETE_FAILED,
                    project_id=project_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return cursor.rowcount > 0
