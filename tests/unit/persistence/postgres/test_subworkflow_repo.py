"""Hermetic unit tests for ``PostgresSubworkflowRepository`` error paths.

Mocks ``psycopg_pool.AsyncConnectionPool`` so no real Postgres is needed.
A raising fake cursor drives the ``psycopg.Error -> QueryError`` translation
across the CRUD surface, plus the ``validate_pagination_args`` reject branch.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowNodeType,
    WorkflowType,
)
from synthorg.persistence.postgres.subworkflow_repo import (
    PostgresSubworkflowRepository,
)
from tests._shared import as_pk, mock_of

pytestmark = pytest.mark.unit

_KEY: tuple[NotBlankStr, NotBlankStr] = (NotBlankStr("wf-test"), NotBlankStr("1"))


def _definition() -> WorkflowDefinition:
    now = datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC)
    return WorkflowDefinition(
        id=as_pk("wf-test"),
        name="Test Workflow",
        description="Test workflow",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        nodes=(
            WorkflowNode(
                id="wf-test-start",
                type=WorkflowNodeType.START,
                label="Start",
                position_x=0.0,
                position_y=0.0,
            ),
            WorkflowNode(
                id="wf-test-end",
                type=WorkflowNodeType.END,
                label="End",
                position_x=100.0,
                position_y=0.0,
            ),
        ),
        edges=(
            WorkflowEdge(
                id="wf-test-e1",
                source_node_id="wf-test-start",
                target_node_id="wf-test-end",
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
        ),
        created_by="admin",
        created_at=now,
        updated_at=now,
        revision=1,
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
        # The subworkflow repo's write methods call ``conn.execute(...)``
        # directly (cursor-less), so the connection itself must raise.
        msg = "connection lost"
        raise psycopg.OperationalError(msg)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        # delete_if_unreferenced wraps its work in ``conn.transaction()``;
        # the body's ``conn.execute`` is what raises.
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
    guarded ``list_items()`` never opens a connection. Used by the
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


def _repo() -> PostgresSubworkflowRepository:
    pool = mock_of[AsyncConnectionPool](connection=_RaisingPool().connection)
    return PostgresSubworkflowRepository(pool)


async def test_save_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().save(_definition())


async def test_get_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().get(_KEY)


async def test_list_items_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().list_items(limit=10, offset=0)


async def test_delete_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().delete(_KEY)


async def test_delete_if_unreferenced_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().delete_if_unreferenced(NotBlankStr("wf-test"), NotBlankStr("1"))


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (-1, 0), (1, -1)],
)
async def test_list_items_rejects_invalid_pagination(limit: int, offset: int) -> None:
    no_io = _NoIOPool()
    repo = PostgresSubworkflowRepository(
        mock_of[AsyncConnectionPool](connection=no_io.connection),
    )
    with pytest.raises(QueryError):
        await repo.list_items(limit=limit, offset=offset)
    # The guard must reject before any connection is opened.
    assert no_io.connection_calls == 0
