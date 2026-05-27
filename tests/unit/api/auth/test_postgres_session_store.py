"""Mock-based unit tests for :class:`PostgresSessionStore`.

Covers protocol compatibility, dispatcher routing, and happy-path SQL
shapes for each operation. Full-fidelity behaviour (concurrent pool
access, real psycopg error types, transaction isolation) is exercised
by integration tests against a real Postgres container; those live
outside the unit test suite.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, override
from unittest.mock import AsyncMock, MagicMock

import psycopg
import pytest
from aiosqlite.core import Connection

from synthorg.core.auth.roles import HumanRole
from synthorg.core.auth.session import Session
from synthorg.core.persistence_errors import QueryError
from synthorg.persistence.auth_protocol import SessionRepository as SessionStore
from synthorg.persistence.postgres.session_repo import (
    PostgresSessionRepository as PostgresSessionStore,
)
from synthorg.persistence.sqlite.session_repo import (
    SQLiteSessionRepository as SqliteSessionStore,
)
from tests._shared import mock_of
from tests._shared.persistence import make_private_write_context

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)


def _make_session(session_id: str = "sess-1", revoked: bool = False) -> Session:
    return Session(
        session_id=session_id,
        user_id="user-1",
        username="alice",
        role=HumanRole.CEO,
        ip_address="127.0.0.1",
        user_agent="test-agent",
        created_at=_NOW,
        last_active_at=_NOW,
        expires_at=_NOW + timedelta(hours=24),
        revoked=revoked,
    )


class _FakeCursor:
    """Minimal async cursor mock supporting execute/fetchone/fetchall."""

    def __init__(self, fetch_rows: list[dict[str, Any]] | None = None) -> None:
        self.rowcount = 0
        self._rows = fetch_rows or []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.executed.append((sql, params))
        # Heuristic: UPDATE/DELETE set rowcount to row count if not
        # explicitly overridden by the test; tests that care override it.
        if self.rowcount == 0 and ("UPDATE" in sql or "DELETE" in sql):
            self.rowcount = len(self._rows) if self._rows else 0

    async def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    async def __aenter__(self) -> _FakeCursor:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


class _FakeConnection:
    """Connection that hands out pre-scripted cursors."""

    def __init__(self, cursors: list[_FakeCursor]) -> None:
        self._cursors = list(cursors)

    def cursor(self, row_factory: Any = None) -> _FakeCursor:
        return self._cursors.pop(0)


class _FakePool:
    """Async-context-manager pool that yields the configured connection."""

    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def connection(self) -> Any:
        yield self._conn


# -- Protocol compatibility ------------------------------------------


def test_postgres_session_store_implements_protocol() -> None:
    """``PostgresSessionStore`` satisfies the :class:`SessionStore` protocol."""
    pool = MagicMock()
    store = PostgresSessionStore(pool)
    assert isinstance(store, SessionStore)


# -- Dispatcher ------------------------------------------------------


def test_concrete_stores_expose_protocol_shape() -> None:
    """Sanity: both concrete stores structurally satisfy ``SessionStore``.

    After A1 consolidation the dispatcher that used to branch on
    handle type is gone; backend-type selection now happens inside
    each persistence backend's ``connect()``.  Cross-backend
    behaviour is covered by ``tests/conformance/persistence``; this
    test just guards against the two classes drifting away from the
    shared protocol (e.g. a renamed or dropped method).
    """
    pg_pool = MagicMock()
    sqlite_db = mock_of[Connection]()
    pg_store = PostgresSessionStore(pg_pool)
    sqlite_store = SqliteSessionStore(
        sqlite_db, write_context=make_private_write_context()
    )
    assert isinstance(pg_store, SessionStore)
    assert isinstance(sqlite_store, SessionStore)
    # Every SessionStore method must exist on both concretes (catches a
    # refactor accident where one silently loses a method via alias).
    for method_name in (
        "load_revoked",
        "save",
        "get",
        "list_by_user",
        "list_all",
        "revoke",
        "revoke_all_for_user",
        "enforce_session_limit",
        "is_revoked",
        "cleanup_expired",
    ):
        assert callable(getattr(pg_store, method_name)), method_name
        assert callable(getattr(sqlite_store, method_name)), method_name


# -- Happy-path SQL shape (one test per method) ----------------------


async def test_create_executes_insert() -> None:
    """``create`` issues an INSERT against the sessions table."""
    cursor = _FakeCursor()
    conn = _FakeConnection([cursor])
    pool = _FakePool(conn)
    store = PostgresSessionStore(pool)  # type: ignore[arg-type]

    await store.save(_make_session())

    assert len(cursor.executed) == 1
    sql, _params = cursor.executed[0]
    assert "INSERT INTO sessions" in sql


async def test_get_returns_session_when_row_present() -> None:
    """``get`` deserializes a matching row into a ``Session``."""
    row = {
        "session_id": "sess-1",
        "user_id": "user-1",
        "username": "alice",
        "role": "ceo",
        "ip_address": "127.0.0.1",
        "user_agent": "test-agent",
        "created_at": _NOW.isoformat(),
        "last_active_at": _NOW.isoformat(),
        "expires_at": (_NOW + timedelta(hours=24)).isoformat(),
        "revoked": False,
    }
    cursor = _FakeCursor(fetch_rows=[row])
    conn = _FakeConnection([cursor])
    pool = _FakePool(conn)
    store = PostgresSessionStore(pool)  # type: ignore[arg-type]

    session = await store.get("sess-1")

    assert session is not None
    assert session.session_id == "sess-1"


async def test_get_returns_none_when_row_absent() -> None:
    """``get`` returns ``None`` when the row does not exist."""
    cursor = _FakeCursor(fetch_rows=[])
    conn = _FakeConnection([cursor])
    pool = _FakePool(conn)
    store = PostgresSessionStore(pool)  # type: ignore[arg-type]

    assert await store.get("missing") is None


async def test_revoke_updates_revoked_set_when_row_affected() -> None:
    """``revoke`` updates the in-memory revoked set on success."""
    cursor = _FakeCursor()
    cursor.rowcount = 1
    conn = _FakeConnection([cursor])
    pool = _FakePool(conn)
    store = PostgresSessionStore(pool)  # type: ignore[arg-type]

    result = await store.revoke("sess-1")

    assert result is True
    assert store.is_revoked("sess-1")


async def test_revoke_returns_false_when_no_row_affected() -> None:
    """``revoke`` returns False and does not mutate the revoked set."""
    cursor = _FakeCursor()
    cursor.rowcount = 0
    conn = _FakeConnection([cursor])
    pool = _FakePool(conn)
    store = PostgresSessionStore(pool)  # type: ignore[arg-type]

    result = await store.revoke("missing")

    assert result is False
    assert not store.is_revoked("missing")


async def test_load_revoked_populates_in_memory_set() -> None:
    """``load_revoked`` hydrates the in-memory set from the DB."""
    rows = [{"session_id": "a"}, {"session_id": "b"}]
    cursor = _FakeCursor(fetch_rows=rows)
    conn = _FakeConnection([cursor])
    pool = _FakePool(conn)
    store = PostgresSessionStore(pool)  # type: ignore[arg-type]

    await store.load_revoked()

    assert store.is_revoked("a")
    assert store.is_revoked("b")


async def test_enforce_session_limit_revokes_oldest() -> None:
    """``enforce_session_limit`` revokes sessions above the cap."""
    now = datetime.now(UTC).isoformat()
    rows = [
        {
            "session_id": f"s{i}",
            "user_id": "user-1",
            "username": "alice",
            "role": "ceo",
            "ip_address": "127.0.0.1",
            "user_agent": "test",
            "created_at": now,
            "last_active_at": now,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "revoked": False,
        }
        for i in range(3)
    ]
    list_cursor = _FakeCursor(fetch_rows=rows)
    revoke_cursor = _FakeCursor()
    revoke_cursor.rowcount = 1
    conn = _FakeConnection([list_cursor, revoke_cursor])
    pool = _FakePool(conn)
    store = PostgresSessionStore(pool)  # type: ignore[arg-type]
    # Stub revoke so we do not need to re-supply cursors for it.
    store.revoke = AsyncMock(return_value=True)  # type: ignore[method-assign]

    revoked = await store.enforce_session_limit("user-1", max_sessions=2)

    assert revoked == 1
    assert store.revoke.await_count == 1


async def test_enforce_session_limit_noop_when_within_cap() -> None:
    """No-op when user is below the session cap."""
    cursor = _FakeCursor(fetch_rows=[])
    conn = _FakeConnection([cursor])
    pool = _FakePool(conn)
    store = PostgresSessionStore(pool)  # type: ignore[arg-type]

    revoked = await store.enforce_session_limit("user-1", max_sessions=5)

    assert revoked == 0


# -- Error translation (psycopg.Error -> QueryError) -----------------


class _RaisingCursor(_FakeCursor):
    """Cursor whose ``execute`` raises a psycopg driver error."""

    @override
    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        msg = "connection lost"
        raise psycopg.OperationalError(msg)


def _raising_store() -> PostgresSessionStore:
    conn = _FakeConnection([_RaisingCursor(), _RaisingCursor()])
    return PostgresSessionStore(_FakePool(conn))  # type: ignore[arg-type]


async def test_save_translates_psycopg_error_to_query_error() -> None:
    """A driver failure in ``save`` surfaces as the domain ``QueryError``."""
    with pytest.raises(QueryError):
        await _raising_store().save(_make_session())


async def test_get_translates_psycopg_error_to_query_error() -> None:
    """A driver failure in ``get`` surfaces as the domain ``QueryError``."""
    with pytest.raises(QueryError):
        await _raising_store().get("sess-1")


async def test_delete_translates_psycopg_error_to_query_error() -> None:
    """A driver failure in ``delete`` surfaces as the domain ``QueryError``."""
    with pytest.raises(QueryError):
        await _raising_store().delete("sess-1")


async def test_load_revoked_translates_psycopg_error_to_query_error() -> None:
    """A driver failure in ``load_revoked`` surfaces as ``QueryError``."""
    with pytest.raises(QueryError):
        await _raising_store().load_revoked()


async def test_cleanup_expired_translates_psycopg_error_to_query_error() -> None:
    """A driver failure in ``cleanup_expired`` surfaces as ``QueryError``."""
    with pytest.raises(QueryError):
        await _raising_store().cleanup_expired()


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (-1, 0), (1, -1)],
)
async def test_list_items_rejects_invalid_pagination(limit: int, offset: int) -> None:
    """Out-of-range pagination args raise ``QueryError`` before any I/O."""
    store = PostgresSessionStore(_FakePool(_FakeConnection([])))  # type: ignore[arg-type]
    with pytest.raises(QueryError):
        await store.list_items(limit=limit, offset=offset)
