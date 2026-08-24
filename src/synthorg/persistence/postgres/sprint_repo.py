# module-kind: repository
"""Postgres repository for agile sprints.

Sibling of :class:`SQLiteSprintRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Satisfies ``SprintRepository``
structurally: id-keyed CRUD, atomic lifecycle transitions, and filtered
queries. Row <-> model marshalling is shared with the SQLite sibling via
:mod:`synthorg.persistence._shared.sprint_marshalling`.
"""

from collections.abc import Mapping
from typing import LiteralString

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.observability import get_logger
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
from synthorg.persistence.postgres._sprint_guards import read_guard, write_guard
from synthorg.persistence.postgres._sprint_sql import (
    ADD_TASK_SQL,
    COMPLETE_TASK_SQL,
    DELETE_SQL,
    GET_SQL,
    LIST_SQL,
    ORDER_BY,
    TRANSITION_SQL,
    UPSERT_SQL,
)
from synthorg.persistence.sprint_protocol import SprintFilterSpec

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000


def _encode_array_jsonb(values: tuple[str, ...]) -> object:
    """Wrap a sprint string-array column for binding to native JSONB.

    Returns:
        A :class:`~psycopg.types.json.Jsonb` adapter.
    """
    return Jsonb(list(values))


def _encode_map_jsonb(values: Mapping[str, float]) -> object:
    """Wrap the ``task_points`` column for binding to native JSONB.

    Returns:
        A :class:`~psycopg.types.json.Jsonb` adapter.
    """
    return Jsonb(dict(values))


class PostgresSprintRepository:
    """Postgres-backed agile sprint repository.

    Args:
        pool: Async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: Sprint) -> None:
        """Upsert a sprint row.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        params = sprint_save_params(
            entity, encode_array=_encode_array_jsonb, encode_map=_encode_map_jsonb
        )
        async with (
            write_guard(operation="save", doing="saving", sprint_id=entity.id),
            self._pool.connection() as conn,
        ):
            await conn.execute(UPSERT_SQL, params)
            await conn.commit()

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
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(GET_SQL, (entity_id,))
            row = await cur.fetchone()
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
        """List sprints newest-first.

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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(LIST_SQL, (effective_limit, offset))
                rows = await cur.fetchall()
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
        where, params = build_sprint_where(filter_spec, placeholder="%s")
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT {SPRINT_COLUMNS} FROM sprints
            WHERE {where}
            {ORDER_BY}
            LIMIT %s OFFSET %s
        """  # noqa: S608 -- ``where`` is a closed set of column predicates
        async with read_guard(operation="query", failure="Failed to query sprints"):
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
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
        where, params = build_sprint_where(filter_spec, placeholder="%s")
        sql = f"SELECT COUNT(*) FROM sprints WHERE {where}"  # noqa: S608
        async with (
            read_guard(operation="count", failure="Failed to count sprints"),
            self._pool.connection() as conn,
            conn.cursor() as cur,
        ):
            await cur.execute(sql, params)
            row = await cur.fetchone()
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
            write_guard(
                operation="transition_if",
                doing="transitioning",
                sprint_id=entity_id,
            ),
            self._pool.connection() as conn,
            conn.cursor() as cur,
        ):
            await cur.execute(TRANSITION_SQL, params)
            rowcount = cur.rowcount
            await conn.commit()
        return rowcount > 0

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
                sprint_id=sprint_id, task_id=task_id, story_points=story_points
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
            write_guard(operation=operation, doing=doing, sprint_id=sprint_id),
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(sql, params)
            row = await cur.fetchone()
            # Parsed BEFORE the commit, inside the pool's connection
            # context so a refusal unwinds the write. These statements
            # DERIVE columns, so they can produce a row the domain model
            # refuses that no input row would have been; the
            # committed-then-parsed order made such a row durable and
            # unreadable at once, failing every later read of it.
            sprint = row_to_sprint(row) if row is not None else None
            await conn.commit()
        return sprint

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a sprint by id.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with (
            write_guard(operation="delete", doing="deleting", sprint_id=entity_id),
            self._pool.connection() as conn,
            conn.cursor() as cur,
        ):
            await cur.execute(DELETE_SQL, (entity_id,))
            rowcount = cur.rowcount
            await conn.commit()
        return rowcount > 0


__all__ = ["PostgresSprintRepository"]
