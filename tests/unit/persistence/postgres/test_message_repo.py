"""Hermetic unit tests for ``PostgresMessageRepository`` error paths.

Mocks ``psycopg_pool.AsyncConnectionPool`` so no real Postgres is needed.
A raising fake cursor drives the ``psycopg.Error -> QueryError`` translation
across the append-only surface, plus the pagination reject branch.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from synthorg.communication.message import Message
from synthorg.core.persistence_errors import QueryError
from synthorg.persistence.message_protocol import MessageFilterSpec
from synthorg.persistence.postgres.message_repo import PostgresMessageRepository
from tests._shared import as_uuid, mock_of, sid
from tests.unit.persistence.conftest import make_message

pytestmark = pytest.mark.unit


def _message() -> Message:
    return make_message(
        msg_id=as_uuid("msg-1"),
        timestamp=datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
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

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _RaisingPool:
    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_RaisingConnection]:
        yield _RaisingConnection()


def _repo() -> PostgresMessageRepository:
    pool = mock_of[AsyncConnectionPool](connection=_RaisingPool().connection)
    return PostgresMessageRepository(pool)


async def test_append_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().append(_message())


async def test_query_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().query(MessageFilterSpec(), limit=10, offset=0)


async def test_purge_before_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().purge_before(datetime(2026, 1, 1, tzinfo=UTC))


async def test_delete_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().delete(sid("msg-1"))


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (-1, 0), (1, -1)],
)
async def test_query_rejects_invalid_pagination(limit: int, offset: int) -> None:
    with pytest.raises(QueryError):
        await _repo().query(MessageFilterSpec(), limit=limit, offset=offset)
