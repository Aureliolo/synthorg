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

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
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
    build_sprint_where,
    complete_task_params,
    row_to_sprint,
    sprint_save_params,
    validate_sprint_update_keys,
)
from synthorg.persistence.sprint_protocol import SprintFilterSpec
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_UPSERT_SQL = f"""
    INSERT INTO sprints ({SPRINT_COLUMNS})
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        project = excluded.project,
        name = excluded.name,
        goal = excluded.goal,
        status = excluded.status,
        sprint_number = excluded.sprint_number,
        duration_days = excluded.duration_days,
        start_date = excluded.start_date,
        end_date = excluded.end_date,
        task_ids = excluded.task_ids,
        completed_task_ids = excluded.completed_task_ids,
        task_points = excluded.task_points,
        story_points_committed = excluded.story_points_committed,
        story_points_completed = excluded.story_points_completed
"""  # noqa: S608 -- column list is a compile-time constant

_TRANSITION_SQL = (
    "UPDATE sprints SET "
    "status = ?, "
    "start_date = COALESCE(?, start_date), "
    "end_date = COALESCE(?, end_date) "
    "WHERE id = ? AND status = ?"
)

# The completion append, guarded in the statement rather than by a prior
# read: ``'$[#]'`` is SQLite's append path, and the two EXISTS predicates
# hold the backlog membership and the not-already-completed invariants
# against the row's own current value.
_COMPLETE_TASK_SQL = f"""
    UPDATE sprints
    SET completed_task_ids = JSON_INSERT(completed_task_ids, '$[#]', ?),
        story_points_completed = story_points_completed + ?
    WHERE id = ?
      AND status IN (?, ?)
      AND EXISTS (SELECT 1 FROM JSON_EACH(sprints.task_ids) WHERE value = ?)
      AND NOT EXISTS (
          SELECT 1 FROM JSON_EACH(sprints.completed_task_ids) WHERE value = ?
      )
    RETURNING {SPRINT_COLUMNS}
"""  # noqa: S608 -- column list is a compile-time constant

_ORDER_BY = "ORDER BY sprint_number DESC, id DESC"


async def _safe_rollback(
    db: aiosqlite.Connection,
    *,
    operation: str,
    **log_context: object,
) -> None:
    """Roll back the current transaction, logging any rollback failure."""
    try:
        await db.rollback()
    except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
        log_exception_redacted(
            logger,
            PERSISTENCE_SPRINT_FAILED,
            rollback_exc,
            phase="rollback",
            operation=operation,
            **log_context,
        )


@asynccontextmanager
async def _write_guard(
    db: aiosqlite.Connection,
    *,
    operation: str,
    doing: str,
    sprint_id: str,
) -> AsyncIterator[None]:
    """Roll back, log, and re-raise a write failure as its domain error.

    Every write on this repository fails the same two ways and owes the
    same three things in the same order, so the sequence lives once. Per
    method, only the words differ.

    Args:
        db: The connection whose transaction is rolled back on failure.
        operation: The repository method, for the structured log.
        doing: The present participle naming the attempt in the message
            an operator reads (``saving``, ``transitioning``, ...).
        sprint_id: The row the write targeted.

    Yields:
        Nothing; the caller runs its statements inside the guard.

    Raises:
        ConstraintViolationError: On a constraint violation.
        QueryError: On any other driver error.
    """
    try:
        yield
    except sqlite3.IntegrityError as exc:
        await _safe_rollback(db, operation=operation, sprint_id=sprint_id)
        msg = (
            f"Constraint violation {doing} sprint {sprint_id!r}: "
            f"{safe_error_description(exc)}"
        )
        logger.warning(
            PERSISTENCE_SPRINT_FAILED,
            operation=operation,
            sprint_id=sprint_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise ConstraintViolationError(msg, constraint=str(exc)) from exc
    except (sqlite3.Error, aiosqlite.Error) as exc:
        await _safe_rollback(db, operation=operation, sprint_id=sprint_id)
        msg = (
            f"Failed {doing} sprint {sprint_id!r}: "
            f"{type(exc).__name__} ({safe_error_description(exc)})"
        )
        logger.warning(
            PERSISTENCE_SPRINT_FAILED,
            operation=operation,
            sprint_id=sprint_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


@asynccontextmanager
async def _read_guard(
    *, operation: str, failure: str, **log_context: object
) -> AsyncIterator[None]:
    """Re-raise a read failure as :class:`QueryError`, logged once.

    The write sibling's second arm without the rollback: a read opened no
    transaction, so there is nothing to unwind. A ``QueryError`` raised
    inside (a row this layer could not parse) passes straight through
    rather than being re-wrapped into a vaguer one.

    Args:
        operation: The repository method, for the structured log.
        failure: The message an operator reads.
        **log_context: Extra structured fields (e.g. ``sprint_id``).

    Yields:
        Nothing; the caller runs its statements inside the guard.

    Raises:
        QueryError: On any driver error.
    """
    try:
        yield
    except QueryError:
        raise
    except (sqlite3.Error, aiosqlite.Error) as exc:
        logger.warning(
            PERSISTENCE_SPRINT_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            **log_context,
        )
        raise QueryError(failure) from exc


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
            _write_guard(
                self._db, operation="save", doing="saving", sprint_id=entity.id
            ),
        ):
            await self._db.execute(_UPSERT_SQL, params)
            await self._db.commit()

    async def get(self, entity_id: NotBlankStr) -> Sprint | None:
        """Get a sprint by id, or ``None`` if not found.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {SPRINT_COLUMNS} FROM sprints WHERE id = ?"  # noqa: S608
        async with (
            _read_guard(
                operation="get",
                failure=f"Failed to fetch sprint {entity_id!r}",
                sprint_id=entity_id,
            ),
            self._db.execute(sql, (entity_id,)) as cursor,
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
        sql = (
            f"SELECT {SPRINT_COLUMNS} FROM sprints "  # noqa: S608
            f"{_ORDER_BY} LIMIT ? OFFSET ?"
        )
        async with _read_guard(
            operation="list_items", failure="Failed to list sprints"
        ):
            async with self._db.execute(sql, (effective_limit, offset)) as cursor:
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
            {_ORDER_BY}
            LIMIT ? OFFSET ?
        """  # noqa: S608 -- ``where`` is a closed set of column predicates
        async with _read_guard(operation="query", failure="Failed to query sprints"):
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
        async with _read_guard(operation="count", failure="Failed to count sprints"):
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
            _write_guard(
                self._db,
                operation="transition_if",
                doing="transitioning",
                sprint_id=entity_id,
            ),
            self._db.execute(_TRANSITION_SQL, params) as cursor,
        ):
            await self._db.commit()
            _db_rowcount = cursor.rowcount
        return _db_rowcount > 0

    async def complete_task_if(
        self,
        sprint_id: NotBlankStr,
        task_id: NotBlankStr,
        story_points: float,
    ) -> Sprint | None:
        """Append *task_id* to ``completed_task_ids`` iff it is absent.

        One conditional statement, so no concurrent writer can slip
        between the check and the append. See the protocol docstring for
        why ``story_points`` is supplied rather than read from the row.

        Returns:
            The sprint after the append, or ``None`` when the guard did
            not match and nothing was written.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        params = complete_task_params(
            sprint_id=sprint_id, task_id=task_id, story_points=story_points
        )
        async with (
            self._write_context(),
            _write_guard(
                self._db,
                operation="complete_task_if",
                doing="completing a task in",
                sprint_id=sprint_id,
            ),
            self._db.execute(_COMPLETE_TASK_SQL, params) as cursor,
        ):
            row = await cursor.fetchone()
            await self._db.commit()
        return row_to_sprint(row) if row is not None else None

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a sprint by id.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM sprints WHERE id = ?"
        async with (
            self._write_context(),
            _write_guard(
                self._db, operation="delete", doing="deleting", sprint_id=entity_id
            ),
            self._db.execute(sql, (entity_id,)) as cursor,
        ):
            await self._db.commit()
            _db_rowcount = cursor.rowcount
        return _db_rowcount > 0


__all__ = ["SQLiteSprintRepository"]
