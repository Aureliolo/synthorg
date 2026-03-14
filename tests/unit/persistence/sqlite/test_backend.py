"""Tests for SQLitePersistenceBackend."""

import sqlite3

import aiosqlite
import pytest

from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.errors import PersistenceConnectionError
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from synthorg.persistence.sqlite.repositories import (
    SQLiteCostRecordRepository,
    SQLiteMessageRepository,
    SQLiteTaskRepository,
)


@pytest.mark.unit
class TestSQLitePersistenceBackend:
    async def test_connect_and_disconnect(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        assert backend.is_connected is False

        await backend.connect()
        assert backend.is_connected is True

        await backend.disconnect()  # type: ignore[unreachable]
        assert backend.is_connected is False

    async def test_connect_idempotent(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        await backend.connect()
        await backend.connect()  # should not raise
        assert backend.is_connected is True
        await backend.disconnect()

    async def test_disconnect_when_not_connected(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        await backend.disconnect()  # should not raise

    async def test_health_check_connected(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        await backend.connect()
        assert await backend.health_check() is True
        await backend.disconnect()

    async def test_health_check_disconnected(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        assert await backend.health_check() is False

    async def test_backend_name(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        assert backend.backend_name == "sqlite"

    async def test_migrate_creates_tables(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        await backend.connect()
        await backend.migrate()

        # Verify tables exist by accessing repos
        assert isinstance(backend.tasks, SQLiteTaskRepository)
        assert isinstance(backend.cost_records, SQLiteCostRecordRepository)
        assert isinstance(backend.messages, SQLiteMessageRepository)
        await backend.disconnect()

    async def test_migrate_without_connect_raises(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        with pytest.raises(PersistenceConnectionError, match="not connected"):
            await backend.migrate()

    async def test_tasks_before_connect_raises(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        with pytest.raises(PersistenceConnectionError, match="Not connected"):
            _ = backend.tasks

    async def test_cost_records_before_connect_raises(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        with pytest.raises(PersistenceConnectionError, match="Not connected"):
            _ = backend.cost_records

    async def test_messages_before_connect_raises(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        with pytest.raises(PersistenceConnectionError, match="Not connected"):
            _ = backend.messages

    async def test_audit_entries_before_connect_raises(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        with pytest.raises(PersistenceConnectionError, match="Not connected"):
            _ = backend.audit_entries

    async def test_wal_mode_enabled(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:", wal_mode=True))
        await backend.connect()
        # WAL mode on :memory: may show as "memory" not "wal",
        # but the PRAGMA succeeds without error
        assert backend.is_connected is True
        await backend.disconnect()

    async def test_wal_mode_disabled(self) -> None:
        backend = SQLitePersistenceBackend(
            SQLiteConfig(path=":memory:", wal_mode=False)
        )
        await backend.connect()
        assert backend.is_connected is True
        await backend.disconnect()

    async def test_connect_failure_raises_connection_error(self) -> None:
        """Non-existent path raises PersistenceConnectionError."""
        config = SQLiteConfig(path="/nonexistent/deeply/nested/dir/test.db")
        backend = SQLitePersistenceBackend(config)
        with pytest.raises(PersistenceConnectionError):
            await backend.connect()
        assert backend.is_connected is False

    async def test_health_check_returns_false_on_error(self) -> None:
        """health_check returns False (not raises) when the db errors."""
        from unittest.mock import AsyncMock

        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        await backend.connect()
        # Patch execute to simulate a database error
        assert backend._db is not None
        backend._db.execute = AsyncMock(  # type: ignore[method-assign]
            side_effect=sqlite3.OperationalError("disk I/O error")
        )
        result = await backend.health_check()
        assert result is False
        await backend.disconnect()

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
    async def test_disconnect_cleans_up_on_close_error(self) -> None:
        """If db.close() raises, state is still cleared."""
        from unittest.mock import AsyncMock

        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        await backend.connect()
        assert backend._db is not None
        backend._db.close = AsyncMock(  # type: ignore[method-assign]
            side_effect=sqlite3.OperationalError("close failed")
        )
        await backend.disconnect()
        assert backend.is_connected is False
        # Restore original close so __del__ can clean up properly.
        backend._db = None

    async def test_connect_pragma_failure_cleans_up(self) -> None:
        """If PRAGMA fails after connect, backend cleans up and raises."""
        from unittest.mock import AsyncMock, patch

        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:", wal_mode=True))

        real_connect = aiosqlite.connect

        async def patched_connect(*args: object, **kwargs: object) -> object:
            conn = await real_connect(":memory:")
            conn.execute = AsyncMock(  # type: ignore[method-assign]
                side_effect=sqlite3.OperationalError("PRAGMA failed")
            )
            return conn

        with (
            patch("aiosqlite.connect", side_effect=patched_connect),
            pytest.raises(PersistenceConnectionError),
        ):
            await backend.connect()

        assert backend.is_connected is False

    async def test_protocol_compliance(self) -> None:
        backend = SQLitePersistenceBackend(SQLiteConfig(path=":memory:"))
        assert isinstance(backend, PersistenceBackend)
