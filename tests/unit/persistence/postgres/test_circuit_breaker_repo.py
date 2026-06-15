"""Hermetic unit tests for ``PostgresCircuitBreakerStateRepository`` error paths.

Mocks ``psycopg_pool.AsyncConnectionPool`` so no real Postgres is needed.
Every method wraps its DB call in ``except psycopg.Error -> QueryError``;
a raising fake cursor drives that translation across the surface, plus the
``validate_pagination_args`` reject branch on ``list_items``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.circuit_breaker_protocol import (
    CircuitBreakerStateRecord,
)
from synthorg.persistence.postgres.circuit_breaker_repo import (
    PostgresCircuitBreakerStateRepository,
)
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_KEY: tuple[NotBlankStr, NotBlankStr] = (NotBlankStr("agent-a"), NotBlankStr("agent-b"))


def _record() -> CircuitBreakerStateRecord:
    return CircuitBreakerStateRecord(
        pair_key_a=NotBlankStr("agent-a"),
        pair_key_b=NotBlankStr("agent-b"),
        bounce_count=1,
        trip_count=0,
        opened_at=None,
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


def _repo() -> PostgresCircuitBreakerStateRepository:
    pool = mock_of[AsyncConnectionPool](connection=_RaisingPool().connection)
    return PostgresCircuitBreakerStateRepository(pool)


async def test_save_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().save(_record())


async def test_get_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().get(_KEY)


async def test_list_items_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().list_items(limit=10, offset=0)


async def test_delete_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().delete(_KEY)


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (-1, 0), (1, -1)],
)
async def test_list_items_rejects_invalid_pagination(limit: int, offset: int) -> None:
    with pytest.raises(QueryError):
        await _repo().list_items(limit=limit, offset=offset)
