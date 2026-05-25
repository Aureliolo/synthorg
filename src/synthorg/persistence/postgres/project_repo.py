"""Postgres repository implementation for Project."""

from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
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

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


def _row_to_project(row: dict[str, Any]) -> Project:
    """Reconstruct a ``Project`` from a Postgres dict_row.

    Returns:
        Result of type ``Project``.
    """
    data = dict(row)
    data["status"] = ProjectStatus(data["status"])
    data["team"] = tuple(data.get("team") or [])
    data["task_ids"] = tuple(data.get("task_ids") or [])
    return Project.model_validate(data)


class PostgresProjectRepository:
    """Postgres-backed project repository.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @staticmethod
    def _row_params(project: Project) -> tuple[object, ...]:
        """Row params.

        Returns:
            Tuple of scalar SQL parameter values for INSERT/UPDATE.
        """
        return (
            project.id,
            project.name,
            project.description,
            Jsonb(list(project.team)),
            project.lead,
            Jsonb(list(project.task_ids)),
            project.deadline,
            project.budget,
            project.status.value,
        )

    async def create(self, project: Project) -> None:
        """Insert a new project, failing if the id already exists.

        Raises:
            DuplicateRecordError: A project with this id already exists.
            QueryError: If the database operation fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO projects (id, name, description, team, lead,
                                          task_ids, deadline, budget, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    self._row_params(project),
                )
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            logger.warning(
                PERSISTENCE_PROJECT_SAVE_FAILED,
                project_id=project.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Project with id {project.id!r} already exists"
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to create project {project.id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_SAVE_FAILED,
                project_id=project.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def update(self, project: Project) -> None:
        """Update an existing project, failing if no row matched.

        Raises:
            RecordNotFoundError: No project with this id exists.
            QueryError: If the database operation fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE projects SET
                        name=%s,
                        description=%s,
                        team=%s,
                        lead=%s,
                        task_ids=%s,
                        deadline=%s,
                        budget=%s,
                        status=%s
                    WHERE id=%s
                    """,
                    (
                        project.name,
                        project.description,
                        Jsonb(list(project.team)),
                        project.lead,
                        Jsonb(list(project.task_ids)),
                        project.deadline,
                        project.budget,
                        project.status.value,
                        project.id,
                    ),
                )
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to update project {project.id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_SAVE_FAILED,
                project_id=project.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if rowcount == 0:
            logger.warning(
                PERSISTENCE_PROJECT_SAVE_FAILED,
                project_id=project.id,
                error_type="RecordNotFoundError",
                error="No project with matching id",
            )
            msg = f"No project with id {project.id!r}"
            raise RecordNotFoundError(msg)

    async def save(self, project: Project) -> None:
        """Persist a project via upsert.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO projects (id, name, description, team, lead,
                                          task_ids, deadline, budget, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(id) DO UPDATE SET
                        name=EXCLUDED.name,
                        description=EXCLUDED.description,
                        team=EXCLUDED.team,
                        lead=EXCLUDED.lead,
                        task_ids=EXCLUDED.task_ids,
                        deadline=EXCLUDED.deadline,
                        budget=EXCLUDED.budget,
                        status=EXCLUDED.status
                    """,
                    self._row_params(project),
                )
                await conn.commit()
        except psycopg.Error as exc:
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
                await cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        except (ValueError, ValidationError, KeyError) as exc:
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
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PROJECT_LIST_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        conditions: list[str] = []
        params: list[object] = []

        if filter_spec.status is not None:
            conditions.append("status = %s")
            params.append(filter_spec.status.value)
        if filter_spec.lead is not None:
            conditions.append("lead = %s")
            params.append(filter_spec.lead)

        # Safety invariant: ``conditions`` only ever contains hardcoded
        # ``"<col> = %s"`` fragments built above; the filter values flow
        # through ``params`` and stay parameterized. Never interpolate
        # user-supplied text into ``conditions``.
        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = f"SELECT * FROM projects{where_clause} ORDER BY id LIMIT %s OFFSET %s"  # noqa: S608
        params.append(effective_limit)
        params.append(offset)

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(query, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list projects"
            logger.warning(
                PERSISTENCE_PROJECT_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            projects = tuple(_row_to_project(row) for row in rows)
        except (ValueError, ValidationError, KeyError) as exc:
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
        """Count projects matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        conditions: list[str] = []
        params: list[object] = []

        if filter_spec.status is not None:
            conditions.append("status = %s")
            params.append(filter_spec.status.value)
        if filter_spec.lead is not None:
            conditions.append("lead = %s")
            params.append(filter_spec.lead)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = f"SELECT COUNT(*) FROM projects{where_clause}"  # noqa: S608

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(query, params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to count projects"
            logger.warning(
                PERSISTENCE_PROJECT_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row["count"]) if row else 0

    async def delete(self, project_id: NotBlankStr) -> bool:
        """Delete a project by primary key.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete project {project_id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_DELETE_FAILED,
                project_id=project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted
