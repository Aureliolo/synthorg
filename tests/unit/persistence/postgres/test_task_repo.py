"""Hermetic unit tests for ``PostgresTaskRepository`` error paths.

Mocks ``psycopg_pool.AsyncConnectionPool`` so no real Postgres is needed.
A raising fake cursor drives the ``psycopg.Error -> QueryError`` translation
across the surface, plus the ``validate_pagination_args`` reject branch.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.persistence.postgres.task_repo import PostgresTaskRepository
from synthorg.persistence.task_protocol import TaskFilterSpec
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit


def _task() -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="Test task",
        description="A hermetic test task.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="manager",
        assigned_to="agent-1",
        status=TaskStatus.ASSIGNED,
    )


class _RaisingCursor:
    async def execute(self, sql: str, params: object = None) -> None:
        msg = "connection lost"
        raise psycopg.OperationalError(msg)

    async def fetchone(self) -> object | None:
        return None

    async def fetchall(self) -> list[object]:
        return []

    async def __aenter__(self) -> _RaisingCursor:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _RaisingConnection:
    def cursor(self, row_factory: object = None) -> _RaisingCursor:
        return _RaisingCursor()

    async def execute(self, sql: str, params: object = None) -> _RaisingCursor:
        msg = "connection lost"
        raise psycopg.OperationalError(msg)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _RaisingPool:
    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_RaisingConnection]:
        yield _RaisingConnection()


class _NoIOPool:
    """Pool whose ``connection()`` fails the test if ever entered.

    Pagination validation must reject BEFORE any I/O, so a correctly
    guarded ``query()`` never opens a connection. Used by the
    invalid-pagination test so its ``QueryError`` can only come from the
    pre-I/O guard, never from a transport error.
    """

    def __init__(self) -> None:
        self.connection_calls = 0

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[object]:
        self.connection_calls += 1
        msg = "connection() entered: pagination guard must reject before I/O"
        raise AssertionError(msg)
        yield  # type: ignore[unreachable]  # pragma: no cover - satisfies generator type


def _repo() -> PostgresTaskRepository:
    pool = mock_of[AsyncConnectionPool](connection=_RaisingPool().connection)
    return PostgresTaskRepository(pool)


async def test_save_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().save(_task())


async def test_get_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().get(sid("task-1"))


async def test_list_items_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().list_items(limit=10, offset=0)


async def test_query_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().query(TaskFilterSpec(), limit=10, offset=0)


async def test_count_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().count(TaskFilterSpec())


async def test_delete_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().delete(sid("task-1"))


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (-1, 0), (1, -1)],
)
async def test_query_rejects_invalid_pagination(limit: int, offset: int) -> None:
    no_io = _NoIOPool()
    repo = PostgresTaskRepository(
        mock_of[AsyncConnectionPool](connection=no_io.connection),
    )
    with pytest.raises(QueryError):
        await repo.query(TaskFilterSpec(), limit=limit, offset=offset)
    # The guard must reject before any connection is opened.
    assert no_io.connection_calls == 0
