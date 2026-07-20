"""Hermetic unit tests for ``PostgresMemoryVectorRepository`` dense recall.

The real pgvector index needs a superuser-provisioned extension and a
Postgres server, so the dual-backend conformance suite deliberately runs
lexical-only. That left the two Postgres dense hazards reachable only by
reading the SQL: the ``%s::vector`` binding (a Python list has no
``float8[] -> vector`` cast, so the embedding must arrive as pgvector's
text form) and the ``CREATE EXTENSION`` privilege wall (pgvector is not a
trusted extension, so a least-privilege production role degrades where CI
and the bundled image succeed). Both are exercised here against a fake
pool that records the SQL and params the repository emits, so a
regression in either fails a fast unit test rather than surfacing only in
production.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
import pytest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from structlog.testing import capture_logs

from synthorg.core.types import NotBlankStr
from synthorg.memory.vector_spec import MemoryVectorSearchSpec
from synthorg.observability.events.memory import (
    MEMORY_DENSE_INDEX_PERMISSION_DENIED,
    MEMORY_DENSE_INDEX_UNAVAILABLE,
)
from synthorg.persistence.postgres.memory_vector_repo import (
    PostgresMemoryVectorRepository,
)
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_AGENT = NotBlankStr("agent-1")
_DIMS = 4


class _RecordingCursor:
    """Cursor that records executed statements and returns no rows."""

    def __init__(self, log: list[tuple[str, object]]) -> None:
        self._log = log

    async def execute(self, statement: str, params: object = None) -> _RecordingCursor:
        self._log.append((statement, params))
        return self

    async def fetchall(self) -> list[object]:
        return []

    async def fetchone(self) -> object | None:
        return None


def _recording_connection(log: list[tuple[str, object]]) -> AsyncConnection:
    """A connection whose statements land in *log* and open no real I/O."""

    async def execute(statement: str, params: object = None) -> _RecordingCursor:
        return await _RecordingCursor(log).execute(statement, params)

    conn: AsyncConnection = mock_of[AsyncConnection](
        execute=execute,
        cursor=lambda row_factory=None: _RecordingCursor(log),
        set_autocommit=_anoop,
    )
    return conn


def _refusing_connection(exc: psycopg.Error) -> AsyncConnection:
    """A connection whose ``CREATE EXTENSION`` raises *exc*."""

    async def execute(statement: str, params: object = None) -> None:
        if statement.startswith("CREATE EXTENSION"):
            raise exc

    conn: AsyncConnection = mock_of[AsyncConnection](
        execute=execute, set_autocommit=_anoop
    )
    return conn


async def _anoop(*_args: object, **_kwargs: object) -> None:
    return None


def _pool_over(conn: AsyncConnection) -> AsyncConnectionPool:
    @asynccontextmanager
    async def _connection() -> AsyncIterator[AsyncConnection]:
        yield conn

    pool: AsyncConnectionPool = mock_of[AsyncConnectionPool](connection=_connection)
    return pool


class TestDenseBinding:
    """The embedding must reach pgvector as text, scoped to the agent."""

    async def test_search_dense_binds_embedding_as_pgvector_text(self) -> None:
        log: list[tuple[str, object]] = []
        repo = PostgresMemoryVectorRepository(_pool_over(_recording_connection(log)))
        await repo.ensure_ready(_DIMS)
        log.clear()

        await repo.search_dense(
            MemoryVectorSearchSpec(
                agent_id=_AGENT,
                embedding=(0.9, 0.1, 0.0, 0.0),
                limit=5,
            )
        )

        statement, params = log[-1]
        assert "<-> %s::vector" in statement
        assert isinstance(params, tuple)
        # The embedding is the first bind and must be pgvector's text form,
        # not a Python list: binding a list to %s::vector fails at runtime.
        assert params[0] == "[0.9,0.1,0.0,0.0]"
        # The agent scope is bound inside the KNN's WHERE, so ownership
        # bounds the k nearest rather than filtering them afterwards.
        assert _AGENT in params
        assert "e.agent_id = %s" in statement
        assert statement.index("agent_id") < statement.index("LIMIT")
        # The limit is the final bind.
        assert params[-1] == 5

    async def test_search_dense_without_embedding_is_empty(self) -> None:
        log: list[tuple[str, object]] = []
        repo = PostgresMemoryVectorRepository(_pool_over(_recording_connection(log)))
        await repo.ensure_ready(_DIMS)
        log.clear()

        hits = await repo.search_dense(MemoryVectorSearchSpec(agent_id=_AGENT))

        assert hits == ()
        assert log == []

    async def test_search_dense_before_ready_is_empty(self) -> None:
        log: list[tuple[str, object]] = []
        repo = PostgresMemoryVectorRepository(_pool_over(_recording_connection(log)))

        hits = await repo.search_dense(
            MemoryVectorSearchSpec(
                agent_id=_AGENT,
                embedding=(1.0, 0.0, 0.0, 0.0),
                limit=5,
            )
        )

        assert hits == ()
        assert repo.supports_dense_search is False


class TestExtensionDegradation:
    """A missing or unpermitted extension degrades, never raises."""

    async def test_privilege_denied_reports_its_own_condition(self) -> None:
        conn = _refusing_connection(
            psycopg.errors.InsufficientPrivilege("permission denied")
        )
        repo = PostgresMemoryVectorRepository(_pool_over(conn))

        with capture_logs() as logs:
            await repo.ensure_ready(_DIMS)

        assert repo.supports_dense_search is False
        denied = [
            entry
            for entry in logs
            if entry["event"] == MEMORY_DENSE_INDEX_PERMISSION_DENIED
        ]
        assert len(denied) == 1
        assert denied[0]["log_level"] == "error"

    async def test_absent_extension_degrades_without_privilege_noise(self) -> None:
        conn = _refusing_connection(
            psycopg.errors.UndefinedFile('extension "vector" is not available')
        )
        repo = PostgresMemoryVectorRepository(_pool_over(conn))

        with capture_logs() as logs:
            await repo.ensure_ready(_DIMS)

        assert repo.supports_dense_search is False
        events = {entry["event"] for entry in logs}
        assert MEMORY_DENSE_INDEX_UNAVAILABLE in events
        assert MEMORY_DENSE_INDEX_PERMISSION_DENIED not in events

    async def test_no_dimensions_stays_lexical_only(self) -> None:
        log: list[tuple[str, object]] = []
        repo = PostgresMemoryVectorRepository(_pool_over(_recording_connection(log)))

        await repo.ensure_ready(None)

        assert repo.supports_dense_search is False
        assert log == []
