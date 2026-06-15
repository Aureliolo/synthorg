"""Hermetic unit tests for ``PostgresAuditRepository`` error paths.

Mocks ``psycopg_pool.AsyncConnectionPool`` so no real Postgres is needed.
A raising fake cursor drives the ``psycopg.Error -> QueryError`` translation
across the append-only surface, plus the ``_validate_query_args`` reject
branches (limit < 1, offset < 0).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.persistence_errors import QueryError
from synthorg.persistence.audit_protocol import AuditFilterSpec
from synthorg.persistence.postgres.audit_repository import PostgresAuditRepository
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.models import AuditEntry, EvaluationConfidence
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_FAKE_HASH = "a" * 64


def _entry() -> AuditEntry:
    return AuditEntry(
        id="audit-1",
        timestamp=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        agent_id="agent-1",
        task_id="task-1",
        tool_name="filesystem.read",
        tool_category=ToolCategory.FILE_SYSTEM,
        action_type="fs:read",
        arguments_hash=_FAKE_HASH,
        verdict="allow",
        risk_level=ApprovalRiskLevel.LOW,
        reason="hermetic test",
        matched_rules=("rule-allowlist",),
        evaluation_duration_ms=1.5,
        confidence=EvaluationConfidence.HIGH,
        approval_id=None,
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


def _repo() -> PostgresAuditRepository:
    pool = mock_of[AsyncConnectionPool](connection=_RaisingPool().connection)
    return PostgresAuditRepository(pool)


async def test_append_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().append(_entry())


async def test_query_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().query(AuditFilterSpec(), limit=10, offset=0)


async def test_purge_before_translates_psycopg_error() -> None:
    with pytest.raises(QueryError):
        await _repo().purge_before(datetime(2026, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (-1, 0), (1, -1)],
)
async def test_query_rejects_invalid_pagination(limit: int, offset: int) -> None:
    with pytest.raises(QueryError):
        await _repo().query(AuditFilterSpec(), limit=limit, offset=offset)
