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
from psycopg._queries import PostgresQuery
from psycopg.adapt import Transformer
from psycopg_pool import AsyncConnectionPool
from structlog.testing import capture_logs

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.memory.vector_spec import MemoryVectorSearchSpec
from synthorg.observability.events.memory import (
    MEMORY_DENSE_INDEX_BUILD_CONTENDED,
    MEMORY_DENSE_INDEX_PERMISSION_DENIED,
    MEMORY_DENSE_INDEX_UNAVAILABLE,
    MEMORY_DENSE_INDEX_UNINDEXABLE,
)
from synthorg.persistence.postgres import _memory_vector_sql as sql
from synthorg.persistence.postgres._memory_vector_sql import (
    HNSW_HALFVEC_MAX_DIMENSIONS,
    HNSW_VECTOR_MAX_DIMENSIONS,
    SELECT_VECTOR_COLUMNS,
    STORAGE_MAX_DIMENSIONS,
    create_vector_index,
    dense_column_spec,
)
from synthorg.persistence.postgres.memory_vector_repo import (
    PostgresMemoryVectorRepository,
)
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_AGENT = NotBlankStr("agent-1")
_DIMS = 4


class _RecordingCursor:
    """Cursor that records executed statements and answers a fixed row."""

    def __init__(
        self,
        log: list[tuple[str, object]],
        row: tuple[object, ...] | None = None,
    ) -> None:
        self._log = log
        self._row = row

    async def execute(self, statement: str, params: object = None) -> _RecordingCursor:
        self._log.append((statement, params))
        return self

    async def fetchall(self) -> list[object]:
        return []

    async def fetchone(self) -> object | None:
        return self._row


def _recording_connection(
    log: list[tuple[str, object]],
    row: tuple[object, ...] | None = None,
) -> AsyncConnection:
    """A connection whose statements land in *log* and open no real I/O."""

    async def execute(statement: str, params: object = None) -> _RecordingCursor:
        return await _RecordingCursor(log, row).execute(statement, params)

    conn: AsyncConnection = mock_of[AsyncConnection](
        execute=execute,
        cursor=lambda row_factory=None: _RecordingCursor(log, row),
        set_autocommit=_anoop,
    )
    return conn


def _raising_connection(
    exc: psycopg.Error,
    on_statement: str,
    row: tuple[object, ...] | None = None,
) -> AsyncConnection:
    """A connection that raises *exc* for *on_statement*, else answers.

    Every other statement returns a cursor rather than ``None``: psycopg
    always hands one back, and a fake that does not turns any new
    ``fetchone`` on an unrelated statement into a spurious failure here.

    Returns:
        The fake connection.
    """

    async def execute(statement: str, params: object = None) -> _RecordingCursor:
        if statement.startswith(on_statement):
            raise exc
        return _RecordingCursor([], row)

    conn: AsyncConnection = mock_of[AsyncConnection](
        execute=execute, set_autocommit=_anoop
    )
    return conn


def _refusing_connection(exc: psycopg.Error) -> AsyncConnection:
    """A connection whose ``CREATE EXTENSION`` raises *exc*.

    Returns:
        The fake connection.
    """
    return _raising_connection(exc, "CREATE EXTENSION")


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


class TestBuildLockContention:
    """A sibling mid-build must not hang the boot that waits for it."""

    async def test_the_lock_wait_is_bounded_before_the_build(self) -> None:
        log: list[tuple[str, object]] = []
        repo = PostgresMemoryVectorRepository(_pool_over(_recording_connection(log)))

        await repo.ensure_ready(_DIMS)

        statements = [statement for statement, _ in log]
        timeout_writes = [
            i
            for i, (statement, _) in enumerate(log)
            if statement == sql.SET_STATEMENT_TIMEOUT
        ]
        armed, restored = timeout_writes
        acquired = statements.index(sql.ACQUIRE_INDEX_BUILD_LOCK)
        # ``pg_advisory_lock`` waits forever and ``lock_timeout`` does not
        # reach it, so the deadline has to be armed before the wait...
        assert statements.index(sql.SHOW_STATEMENT_TIMEOUT) < armed < acquired
        assert acquired < restored
        # ...and lifted before the build, which is legitimately long.
        assert restored < next(
            i for i, s in enumerate(statements) if s.startswith("CREATE INDEX")
        )
        assert log[armed][1] == (sql.INDEX_BUILD_LOCK_WAIT,)

    async def test_the_prior_statement_timeout_is_restored_not_reset(self) -> None:
        # RESET restores the *database* default, so a pooled connection
        # carrying a caller-set timeout would lose it for every query it
        # served afterwards.
        log: list[tuple[str, object]] = []
        conn = _recording_connection(log, row=("42s",))
        repo = PostgresMemoryVectorRepository(_pool_over(conn))

        await repo.ensure_ready(_DIMS)

        restored = [
            params
            for statement, params in log
            if statement == sql.SET_STATEMENT_TIMEOUT
        ][-1]
        assert restored == ("42s",)

    async def test_a_held_lock_degrades_instead_of_blocking(self) -> None:
        # What Postgres raises once ``statement_timeout`` fires on a wait
        # the holder never releases in time.
        cancelled = "canceling statement due to statement timeout"
        conn = _raising_connection(
            psycopg.errors.QueryCanceled(cancelled),
            sql.ACQUIRE_INDEX_BUILD_LOCK,
        )
        repo = PostgresMemoryVectorRepository(_pool_over(conn))

        with capture_logs() as logs:
            await repo.ensure_ready(_DIMS)

        assert repo.supports_dense_search is False
        events = {entry["event"] for entry in logs}
        # Reported as contention, not as the unavailability an operator
        # would read as "pgvector is broken": this one clears itself once
        # the other builder finishes.
        assert MEMORY_DENSE_INDEX_BUILD_CONTENDED in events


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


class TestOrphanScanQuery:
    """The orphan scan's SQL must survive psycopg's placeholder parser."""

    def test_the_column_pattern_survives_placeholder_binding(self) -> None:
        # psycopg parses the query for client-side placeholders before
        # binding, so a bare ``%`` beside a quote reads as a malformed one and
        # raises. That is invisible to the recording cursor these tests use,
        # and the scan swallows psycopg errors, so the whole diagnostic went
        # unrun in production without a single failure to show for it.
        pgq = PostgresQuery(Transformer())
        pgq.convert(SELECT_VECTOR_COLUMNS, ("embedding_768",))

        assert b"LIKE 'embedding\\_%'" in pgq.query
        assert pgq.params is not None
        assert len(pgq.params) == 1


class TestDenseWidthStrategy:
    """Storage and indexing follow pgvector's per-type HNSW ceilings."""

    def test_full_precision_below_the_vector_ceiling(self) -> None:
        spec = dense_column_spec(HNSW_VECTOR_MAX_DIMENSIONS)

        assert spec.element_type == "vector"
        assert spec.indexable is True
        assert spec.name == f"embedding_{HNSW_VECTOR_MAX_DIMENSIONS}"

    def test_half_precision_between_the_ceilings(self) -> None:
        spec = dense_column_spec(HNSW_VECTOR_MAX_DIMENSIONS + 1)

        assert spec.element_type == "halfvec"
        assert spec.indexable is True
        # A distinct name: ADD COLUMN IF NOT EXISTS would otherwise keep a
        # full-precision column an earlier build left at the same width.
        assert spec.name.startswith("embedding_h")

    def test_half_precision_at_the_halfvec_ceiling(self) -> None:
        # The last indexable width; one more falls to an exact scan.
        spec = dense_column_spec(HNSW_HALFVEC_MAX_DIMENSIONS)

        assert spec.element_type == "halfvec"
        assert spec.indexable is True

    def test_above_every_ceiling_is_unindexable(self) -> None:
        spec = dense_column_spec(HNSW_HALFVEC_MAX_DIMENSIONS + 1)

        assert spec.indexable is False
        with pytest.raises(ValueError, match="HNSW ceiling"):
            create_vector_index(spec)

    def test_beyond_the_storage_ceiling_is_refused(self) -> None:
        # No column definition could satisfy it, so this fails here rather
        # than inside ALTER TABLE as a generic driver error the repository
        # degrades on without saying why.
        with pytest.raises(ValueError, match="storage ceiling"):
            dense_column_spec(STORAGE_MAX_DIMENSIONS + 1)

    async def test_unindexable_width_still_supports_dense_search(self) -> None:
        log: list[tuple[str, object]] = []
        repo = PostgresMemoryVectorRepository(_pool_over(_recording_connection(log)))

        with capture_logs() as logs:
            await repo.ensure_ready(HNSW_HALFVEC_MAX_DIMENSIONS + 1)

        # Exact scan beats no semantic recall at all, so the column is built
        # and dense search stays on; the missing index is reported loudly.
        assert repo.supports_dense_search is True
        # But not as indexed: health reports DEGRADED off this, because
        # correct-but-full-scan recall looks identical in its answers.
        assert repo.dense_search_indexed is False
        assert not [s for s, _ in log if s.startswith("CREATE INDEX")]
        assert [s for s, _ in log if "ADD COLUMN" in s]
        assert [
            entry
            for entry in logs
            if entry.get("event") == MEMORY_DENSE_INDEX_UNINDEXABLE
            and entry.get("log_level") == "error"
        ]

    async def test_an_indexed_width_reports_itself_as_indexed(self) -> None:
        log: list[tuple[str, object]] = []
        repo = PostgresMemoryVectorRepository(_pool_over(_recording_connection(log)))

        await repo.ensure_ready(HNSW_VECTOR_MAX_DIMENSIONS)

        assert repo.dense_search_indexed is True

    async def test_half_precision_width_binds_halfvec(self) -> None:
        log: list[tuple[str, object]] = []
        repo = PostgresMemoryVectorRepository(_pool_over(_recording_connection(log)))
        width = HNSW_VECTOR_MAX_DIMENSIONS + 1
        await repo.ensure_ready(width)
        log.clear()

        await repo.search_dense(
            MemoryVectorSearchSpec(
                agent_id=_AGENT,
                embedding=(0.0,) * width,
                limit=5,
            )
        )

        statement, _params = log[-1]
        assert "<-> %s::halfvec" in statement


class _FailingCursor:
    """Cursor whose ``execute`` always raises the given psycopg error."""

    def __init__(self, exc: psycopg.Error) -> None:
        self._exc = exc

    async def execute(self, statement: str, params: object = None) -> object:
        raise self._exc


def _failing_connection(exc: psycopg.Error) -> AsyncConnection:
    """A connection whose cursor statements raise *exc*."""
    conn: AsyncConnection = mock_of[AsyncConnection](
        cursor=lambda row_factory=None: _FailingCursor(exc),
        set_autocommit=_anoop,
    )
    return conn


class TestQueryErrorPaths:
    """A failed statement surfaces as a typed ``QueryError``.

    Parity with the SQLite arm: a raw ``psycopg.Error`` must never cross
    the persistence boundary; the repository wraps it so callers depend on
    the typed error, not the driver's exception hierarchy.
    """

    async def test_count_raises_query_error(self) -> None:
        repo = PostgresMemoryVectorRepository(
            _pool_over(_failing_connection(psycopg.OperationalError("boom")))
        )

        with pytest.raises(QueryError):
            await repo.count(_AGENT)

    async def test_get_raises_query_error(self) -> None:
        repo = PostgresMemoryVectorRepository(
            _pool_over(_failing_connection(psycopg.OperationalError("boom")))
        )

        with pytest.raises(QueryError):
            await repo.get(_AGENT, NotBlankStr("m1"))
