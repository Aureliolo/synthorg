"""Hermetic unit tests for ``PostgresWorkflowExecutionRepository`` error paths.

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
from synthorg.engine.workflow.enums import (
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.engine.workflow.execution_models import (
    WorkflowExecution,
    WorkflowNodeExecution,
)
from synthorg.persistence.postgres.workflow_execution_repo import (
    PostgresWorkflowExecutionRepository,
)
from synthorg.persistence.workflow_execution_protocol import (
    WorkflowExecutionFilterSpec,
)
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit


def _execution() -> WorkflowExecution:
    now = datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC)
    return WorkflowExecution(
        id=as_uuid("exec-1"),
        definition_id=sid("wf-test"),
        definition_revision=1,
        status=WorkflowExecutionStatus.RUNNING,
        node_executions=(
            WorkflowNodeExecution(
                node_id="wf-test-start",
                node_type=WorkflowNodeType.START,
                status=WorkflowNodeExecutionStatus.COMPLETED,
            ),
        ),
        activated_by="admin",
        project="test-project",
        created_at=now,
        updated_at=now,
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


def _repo() -> PostgresWorkflowExecutionRepository:
    pool = mock_of[AsyncConnectionPool](connection=_RaisingPool().connection)
    return PostgresWorkflowExecutionRepository(pool)


async def test_save_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().save(_execution())


async def test_get_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().get(sid("exec-1"))


async def test_list_items_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().list_items(limit=10, offset=0)


async def test_query_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().query(WorkflowExecutionFilterSpec(), limit=10, offset=0)


async def test_count_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().count(WorkflowExecutionFilterSpec())


async def test_delete_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().delete(sid("exec-1"))


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (-1, 0), (1, -1)],
)
async def test_query_rejects_invalid_pagination(limit: int, offset: int) -> None:
    with pytest.raises(QueryError):
        await _repo().query(WorkflowExecutionFilterSpec(), limit=limit, offset=offset)
