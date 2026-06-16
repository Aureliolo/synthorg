"""Hermetic unit tests for ``PostgresCostRecordRepository`` error paths.

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

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.core.persistence_errors import QueryError
from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec
from synthorg.persistence.postgres.cost_record_repo import PostgresCostRecordRepository
from tests._shared import mock_of, sid

pytestmark = pytest.mark.unit


def _record() -> CostRecord:
    return CostRecord(
        agent_id="agent-1",
        task_id=sid("task-1"),
        provider="test-provider",
        model="test-small-001",
        input_tokens=100,
        output_tokens=50,
        cost=0.05,
        currency="EUR",
        timestamp=datetime(2026, 4, 10, 12, tzinfo=UTC),
        call_category=LLMCallCategory.PRODUCTIVE,
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


def _repo() -> PostgresCostRecordRepository:
    pool = mock_of[AsyncConnectionPool](connection=_RaisingPool().connection)
    return PostgresCostRecordRepository(pool)


async def test_append_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().append(_record())


async def test_query_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().query(CostRecordFilterSpec(), limit=10, offset=0)


async def test_aggregate_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().aggregate(agent_id="agent-1")


async def test_purge_before_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().purge_before(datetime(2026, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (-1, 0), (1, -1)],
)
async def test_query_rejects_invalid_pagination(limit: int, offset: int) -> None:
    with pytest.raises(QueryError):
        await _repo().query(CostRecordFilterSpec(), limit=limit, offset=offset)
