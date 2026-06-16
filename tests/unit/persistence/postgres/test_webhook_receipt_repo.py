"""Hermetic unit tests for ``PostgresWebhookReceiptRepository`` error paths.

Mocks ``psycopg_pool.AsyncConnectionPool`` so no real Postgres is needed.
A raising fake cursor drives the ``psycopg.Error -> QueryError`` translation
across the surface, plus the ``validate_pagination_args`` reject branch.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import WebhookReceipt
from synthorg.persistence.postgres.webhook_receipt_repo import (
    PostgresWebhookReceiptRepository,
)
from tests._shared import as_pk, mock_of, sid

pytestmark = pytest.mark.unit


def _receipt() -> WebhookReceipt:
    return WebhookReceipt(
        id=as_pk("rcpt-001"),
        connection_name=NotBlankStr("github-bot"),
        event_type="push",
        status="received",
        received_at=datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC),
        payload_json='{"event":"push"}',
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


def _repo() -> PostgresWebhookReceiptRepository:
    pool = mock_of[AsyncConnectionPool](connection=_RaisingPool().connection)
    return PostgresWebhookReceiptRepository(pool)


async def test_save_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().save(_receipt())


async def test_get_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().get(sid("rcpt-001"))


async def test_list_items_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().list_items(limit=10, offset=0)


async def test_delete_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().delete(sid("rcpt-001"))


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (-1, 0), (1, -1)],
)
async def test_list_items_rejects_invalid_pagination(limit: int, offset: int) -> None:
    with pytest.raises(QueryError):
        await _repo().list_items(limit=limit, offset=offset)
