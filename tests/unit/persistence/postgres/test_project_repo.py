"""Hermetic unit tests for ``PostgresProjectRepository`` error paths.

Mocks ``psycopg_pool.AsyncConnectionPool`` so no real Postgres is
needed. Every method wraps its DB call in ``except psycopg.Error ->
QueryError``; a raising fake cursor drives that translation across the
whole surface, plus the ``validate_pagination_args`` reject branch.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.persistence.postgres.project_repo import PostgresProjectRepository
from synthorg.persistence.project_protocol import ProjectFilterSpec
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit


def _project(project_id: str = "proj-001") -> Project:
    return Project(
        id=as_uuid(project_id),
        name="Test Project",
        description="A test project",
        team=(),
        lead=None,
        task_ids=(),
        deadline=None,
        budget=0.0,
        status=ProjectStatus.PLANNING,
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

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _RaisingPool:
    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_RaisingConnection]:
        yield _RaisingConnection()


def _repo() -> PostgresProjectRepository:
    pool = mock_of[AsyncConnectionPool](connection=_RaisingPool().connection)
    return PostgresProjectRepository(pool)


async def test_create_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().create(_project())


async def test_update_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().update(_project())


async def test_save_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().save(_project())


async def test_get_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().get(sid("proj-001"))


async def test_delete_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().delete(sid("proj-001"))


async def test_list_items_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().list_items(limit=10, offset=0)


async def test_query_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().query(ProjectFilterSpec(), limit=10, offset=0)


async def test_count_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().count(ProjectFilterSpec())


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (-1, 0), (1, -1)],
)
async def test_list_items_rejects_invalid_pagination(limit: int, offset: int) -> None:
    with pytest.raises(QueryError):
        await _repo().list_items(limit=limit, offset=offset)


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (-1, 0), (1, -1)],
)
async def test_query_rejects_invalid_pagination(limit: int, offset: int) -> None:
    with pytest.raises(QueryError):
        await _repo().query(ProjectFilterSpec(), limit=limit, offset=offset)
