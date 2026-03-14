"""Tests for V6 migration (checkpoints and heartbeats tables)."""

from typing import TYPE_CHECKING

import pytest

from synthorg.persistence.sqlite.migrations import (
    SCHEMA_VERSION,
    run_migrations,
)

if TYPE_CHECKING:
    import aiosqlite

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestSchemaVersion:
    def test_schema_version_is_seven(self) -> None:
        assert SCHEMA_VERSION == 7


@pytest.mark.unit
class TestV6MigrationCheckpointsTable:
    """V6 migration creates the checkpoints table."""

    async def test_creates_checkpoints_table(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        await run_migrations(memory_db)
        cursor = await memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoints'"
        )
        row = await cursor.fetchone()
        assert row is not None

    async def test_checkpoints_table_columns(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        """Verify the checkpoints table has the expected columns."""
        await run_migrations(memory_db)
        cursor = await memory_db.execute("PRAGMA table_info(checkpoints)")
        columns = {row[1] for row in await cursor.fetchall()}
        expected = {
            "id",
            "execution_id",
            "agent_id",
            "task_id",
            "turn_number",
            "context_json",
            "created_at",
        }
        assert expected == columns


@pytest.mark.unit
class TestV6MigrationHeartbeatsTable:
    """V6 migration creates the heartbeats table."""

    async def test_creates_heartbeats_table(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        await run_migrations(memory_db)
        cursor = await memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='heartbeats'"
        )
        row = await cursor.fetchone()
        assert row is not None

    async def test_heartbeats_table_columns(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        """Verify the heartbeats table has the expected columns."""
        await run_migrations(memory_db)
        cursor = await memory_db.execute("PRAGMA table_info(heartbeats)")
        columns = {row[1] for row in await cursor.fetchall()}
        expected = {
            "execution_id",
            "agent_id",
            "task_id",
            "last_heartbeat_at",
        }
        assert expected == columns


@pytest.mark.unit
class TestV6MigrationIndexes:
    """V6 migration creates the expected indexes."""

    async def test_creates_checkpoint_indexes(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        await run_migrations(memory_db)
        cursor = await memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'idx_cp_%' ORDER BY name"
        )
        indexes = {row[0] for row in await cursor.fetchall()}
        expected = {
            "idx_cp_execution_id",
            "idx_cp_task_id",
            "idx_cp_exec_turn",
            "idx_cp_task_turn",
        }
        assert expected.issubset(indexes)

    async def test_creates_heartbeat_index(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        await run_migrations(memory_db)
        cursor = await memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'idx_hb_%' ORDER BY name"
        )
        indexes = {row[0] for row in await cursor.fetchall()}
        assert "idx_hb_last_heartbeat" in indexes


@pytest.mark.unit
class TestV6MigrationIdempotent:
    """Running migrations twice is safe."""

    async def test_idempotent(self, memory_db: aiosqlite.Connection) -> None:
        await run_migrations(memory_db)
        # Second run should not fail
        await run_migrations(memory_db)

        # Tables should still be there
        cursor = await memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('checkpoints', 'heartbeats') ORDER BY name"
        )
        tables = [row[0] for row in await cursor.fetchall()]
        assert "checkpoints" in tables
        assert "heartbeats" in tables
