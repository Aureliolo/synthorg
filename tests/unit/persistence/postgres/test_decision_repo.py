"""Hermetic unit tests for ``PostgresDecisionRepository`` error paths.

Mocks ``psycopg_pool.AsyncConnectionPool`` so no real Postgres is needed.
A raising fake cursor drives the ``psycopg.Error -> QueryError`` translation
across the append / query / purge surface, plus the pagination reject branch.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.decisions import DecisionOutcome, DecisionRecord
from synthorg.persistence.decision_protocol import DecisionFilterSpec
from synthorg.persistence.postgres.decision import PostgresDecisionRepository
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC)


def _record() -> DecisionRecord:
    return DecisionRecord(
        id=as_uuid("dec-1"),
        task_id=NotBlankStr(sid("task-1")),
        approval_id=None,
        executing_agent_id=NotBlankStr("ex"),
        reviewer_agent_id=NotBlankStr("rv"),
        decision=DecisionOutcome.APPROVED,
        reason=None,
        criteria_snapshot=(),
        recorded_at=_NOW,
        version=1,
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
        yield  # pragma: no cover - unreachable; satisfies the generator type


def _repo() -> PostgresDecisionRepository:
    pool = mock_of[AsyncConnectionPool](connection=_RaisingPool().connection)
    return PostgresDecisionRepository(pool)


async def test_append_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().append(_record())


async def test_get_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().get(sid("dec-1"))


async def test_query_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().query(DecisionFilterSpec(), limit=10, offset=0)


async def test_purge_before_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().purge_before(datetime(2026, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (-1, 0), (1, -1)],
)
async def test_query_rejects_invalid_pagination(limit: int, offset: int) -> None:
    no_io = _NoIOPool()
    repo = PostgresDecisionRepository(
        mock_of[AsyncConnectionPool](connection=no_io.connection),
    )
    with pytest.raises(QueryError):
        await repo.query(DecisionFilterSpec(), limit=limit, offset=offset)
    # The guard must reject before any connection is opened.
    assert no_io.connection_calls == 0
