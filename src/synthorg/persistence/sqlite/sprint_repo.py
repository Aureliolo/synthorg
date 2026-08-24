# module-kind: repository
"""SQLite repository for agile sprints.

Satisfies ``SprintRepository`` structurally: id-keyed CRUD, atomic
lifecycle transitions (``planning -> active -> in_review ->
retrospective -> completed``), and filtered queries by project / status.

Tuple-valued fields (``task_ids``, ``completed_task_ids``) are stored as
JSON arrays and ``task_points`` as a JSON object; ISO-8601 date strings
and story points are flattened into dedicated columns. Row <-> model
marshalling is shared with the Postgres sibling via
:mod:`synthorg.persistence._shared.sprint_marshalling`.
"""

from typing import LiteralString

import aiosqlite

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.observability import (
    get_logger,
)
from synthorg.observability.events.persistence.sprint import (
    PERSISTENCE_SPRINT_FAILED,
    PERSISTENCE_SPRINT_FETCHED,
    PERSISTENCE_SPRINT_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence._shared.sprint_marshalling import (
    SPRINT_COLUMNS,
    add_task_params,
    build_sprint_where,
    complete_task_params,
    row_to_sprint,
    sprint_save_params,
    validate_sprint_update_keys,
)
from synthorg.persistence.sprint_protocol import SprintFilterSpec
from synthorg.persistence.sqlite._shared import WriteContext
from synthorg.persistence.sqlite._sprint_guards import read_guard, write_guard
from synthorg.persistence.sqlite._sprint_sql import (
    ADD_TASK_SQL,
    COMPLETE_TASK_SQL,
    DELETE_SQL,
    GET_SQL,
    LIST_SQL,
    ORDER_BY,
    TRANSITION_SQL,
    UPSERT_SQL,
)

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000


class SQLiteSprintRepository:
    """SQLite-backed agile sprint repository.

    Args:
        db: An open aiosqlite connection.
        write_context: Async write-serialising context manager.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context

    async def save(self, entity: Sprint) -> None:
        """Upsert a sprint row.

        Raises:
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        params = sprint_save_params(entity)
        async with (
            self._write_context(),
            write_guard(
                self._db, operation="save", doing="saving", sprint_id=entity.id
            ),
        ):
            await self._db.execute(UPSERT_SQL, params)
            await self._db.commit()

    async def get(self, entity_id: NotBlankStr) -> Sprint | None:
        """Get a sprint by id, or ``None`` if not found.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        async with (
            read_guard(
                operation="get",
                failure=f"Failed to fetch sprint {entity_id!r}",
                sprint_id=entity_id,
            ),
            self._db.execute(GET_SQL, (entity_id,)) as cursor,
        ):
            row = await cursor.fetchone()
        if row is None:
            return None
        sprint = row_to_sprint(row)
        logger.debug(PERSISTENCE_SPRINT_FETCHED, sprint_id=entity_id)
        return sprint

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Sprint, ...]:
        """List sprints newest-first (``sprint_number DESC, id DESC``).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_SPRINT_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        async with read_guard(operation="list_items", failure="Failed to list sprints"):
            async with self._db.execute(LIST_SQL, (effective_limit, offset)) as cursor:
                rows = await cursor.fetchall()
            items = tuple(row_to_sprint(r) for r in rows)
        logger.debug(PERSISTENCE_SPRINT_LISTED, count=len(items))
        return items

    async def query(
        self,
        filter_spec: SprintFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Sprint, ...]:
        """Return sprints matching the spec, newest-first (paginated).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_SPRINT_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        where, params = build_sprint_where(filter_spec, placeholder="?")
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT {SPRINT_COLUMNS} FROM sprints
            WHERE {where}
            {ORDER_BY}
            LIMIT ? OFFSET ?
        """  # noqa: S608 -- ``where`` is a closed set of column predicates
        async with read_guard(operation="query", failure="Failed to query sprints"):
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
            items = tuple(row_to_sprint(r) for r in rows)
        logger.debug(PERSISTENCE_SPRINT_LISTED, count=len(items))
        return items

    async def count(self, filter_spec: SprintFilterSpec) -> int:
        """Count sprints matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = build_sprint_where(filter_spec, placeholder="?")
        sql = f"SELECT COUNT(*) FROM sprints WHERE {where}"  # noqa: S608
        async with read_guard(operation="count", failure="Failed to count sprints"):
            async with self._db.execute(sql, params) as cursor:
                row = await cursor.fetchone()
            assert row is not None  # noqa: S101 -- COUNT always returns a row
            return int(row[0])

    async def transition_if(
        self,
        entity_id: NotBlankStr,
        from_state: SprintStatus,
        to_state: SprintStatus,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the sprint lifecycle state.

        ``**updates`` carries the date columns stamped at the transition
        (``start_date`` on activation, ``end_date`` on completion). Each
        is applied via ``COALESCE`` so a missing key leaves the column
        unchanged.

        Returns:
            ``True`` when the operation succeeded, ``False`` otherwise.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        validate_sprint_update_keys(updates)
        params = (
            to_state.value,
            updates.get("start_date"),
            updates.get("end_date"),
            entity_id,
            from_state.value,
        )
        async with (
            self._write_context(),
            write_guard(
                self._db,
                operation="transition_if",
                doing="transitioning",
                sprint_id=entity_id,
            ),
            self._db.execute(TRANSITION_SQL, params) as cursor,
        ):
            await self._db.commit()
            _db_rowcount = cursor.rowcount
        return _db_rowcount > 0

    async def complete_task_if(
        self,
        sprint_id: NotBlankStr,
        task_id: NotBlankStr,
    ) -> Sprint | None:
        """Append *task_id* to ``completed_task_ids`` iff it is absent.

        One conditional statement, so no concurrent writer can slip
        between the check and the append. See the protocol docstring for
        why the points total is re-derived rather than accumulated.

        Returns:
            The sprint after the append, or ``None`` when the guard did
            not match and nothing was written.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        return await self._guarded_backlog_write(
            COMPLETE_TASK_SQL,
            complete_task_params(sprint_id=sprint_id, task_id=task_id),
            operation="complete_task_if",
            doing="completing a task in",
            sprint_id=sprint_id,
        )

    async def add_task_if_planning(
        self,
        sprint_id: NotBlankStr,
        task_id: NotBlankStr,
        story_points: float,
        max_tasks: int,
    ) -> Sprint | None:
        """Append *task_id* to the backlog iff the sprint is still PLANNING.

        Returns:
            The sprint after the append, or ``None`` when the guard did
            not match and nothing was written.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        return await self._guarded_backlog_write(
            ADD_TASK_SQL,
            add_task_params(
                sprint_id=sprint_id,
                task_id=task_id,
                story_points=story_points,
                max_tasks=max_tasks,
            ),
            operation="add_task_if_planning",
            doing="adding a task to",
            sprint_id=sprint_id,
        )

    async def _guarded_backlog_write(
        self,
        sql: LiteralString,
        params: tuple[object, ...],
        *,
        operation: str,
        doing: str,
        sprint_id: str,
    ) -> Sprint | None:
        """Run a conditional backlog UPDATE and marshal its RETURNING row.

        Args:
            sql: The guarded statement.
            params: Its positional params.
            operation: The repository method, for the structured log.
            doing: The present participle for the operator-facing message.
            sprint_id: The row the write targeted.

        Returns:
            The post-image sprint, or ``None`` when the guard did not match.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        async with (
            self._write_context(),
            write_guard(
                self._db, operation=operation, doing=doing, sprint_id=sprint_id
            ),
            self._db.execute(sql, params) as cursor,
        ):
            row = await cursor.fetchone()
            # Parsed BEFORE the commit, and inside the guard. These
            # statements DERIVE columns, so they can produce a row the
            # domain model refuses that no input row would have been; the
            # committed-then-parsed order made such a row durable and
            # unreadable at once, failing every later read of it.
            sprint = row_to_sprint(row) if row is not None else None
            await self._db.commit()
        return sprint

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a sprint by id.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with (
            self._write_context(),
            write_guard(
                self._db, operation="delete", doing="deleting", sprint_id=entity_id
            ),
            self._db.execute(DELETE_SQL, (entity_id,)) as cursor,
        ):
            await self._db.commit()
            _db_rowcount = cursor.rowcount
        return _db_rowcount > 0


__all__ = ["SQLiteSprintRepository"]
