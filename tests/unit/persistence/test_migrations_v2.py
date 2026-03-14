"""Tests for v2+ schema migrations."""

from typing import TYPE_CHECKING

import aiosqlite
import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from ai_company.persistence.sqlite.migrations import (
    SCHEMA_VERSION,
    _apply_v1,
    get_user_version,
    run_migrations,
    set_user_version,
)

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


@pytest.fixture
async def memory_db() -> AsyncGenerator[aiosqlite.Connection]:
    """Raw in-memory SQLite connection (no migrations)."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    yield conn
    await conn.close()


class TestSchemaMigrations:
    async def test_schema_version_is_seven(self) -> None:
        assert SCHEMA_VERSION == 7

    async def test_fresh_db_creates_all_v2_tables(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        """Running migrations on a fresh database creates v2 tables."""
        await run_migrations(memory_db)

        cursor = await memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in await cursor.fetchall()}

        assert "lifecycle_events" in tables
        assert "task_metrics" in tables
        assert "collaboration_metrics" in tables
        # v1 tables still present
        assert "tasks" in tables
        assert "cost_records" in tables
        assert "messages" in tables

    async def test_v1_to_v2_migration(self, memory_db: aiosqlite.Connection) -> None:
        """Manually applying v1, then running full migrations adds v2 tables."""
        await _apply_v1(memory_db)
        await set_user_version(memory_db, 1)
        await memory_db.commit()
        assert await get_user_version(memory_db) == 1

        await run_migrations(memory_db)
        assert await get_user_version(memory_db) == SCHEMA_VERSION

        cursor = await memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in await cursor.fetchall()}
        assert "lifecycle_events" in tables
        assert "task_metrics" in tables
        assert "collaboration_metrics" in tables

    async def test_idempotent_rerun(self, memory_db: aiosqlite.Connection) -> None:
        """Running migrations twice does not raise an error."""
        await run_migrations(memory_db)
        await run_migrations(memory_db)
        assert await get_user_version(memory_db) == SCHEMA_VERSION

    async def test_v2_indexes_created(self, memory_db: aiosqlite.Connection) -> None:
        """All v2 indexes are present after migration."""
        await run_migrations(memory_db)

        cursor = await memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'idx_%' ORDER BY name"
        )
        indexes = {row[0] for row in await cursor.fetchall()}

        expected_v2 = {
            "idx_le_agent_id",
            "idx_le_event_type",
            "idx_le_timestamp",
            "idx_tm_agent_id",
            "idx_tm_completed_at",
            "idx_cm_agent_id",
            "idx_cm_recorded_at",
        }
        assert expected_v2.issubset(indexes)

    async def test_v7_makes_task_id_nullable(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        """v7 migration makes parked_contexts.task_id nullable."""
        # Simulate a pre-v7 database with NOT NULL task_id
        await memory_db.execute("""\
CREATE TABLE parked_contexts (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    parked_at TEXT NOT NULL,
    context_json TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
)""")
        await set_user_version(memory_db, 6)
        await memory_db.commit()

        # Verify task_id is NOT NULL before migration
        cursor = await memory_db.execute("PRAGMA table_info('parked_contexts')")
        cols = {row[1]: row[3] for row in await cursor.fetchall()}
        assert cols["task_id"] == 1  # notnull=1

        # Run migrations (applies v7)
        await run_migrations(memory_db)
        assert await get_user_version(memory_db) == 7

        # Verify task_id is now nullable
        cursor = await memory_db.execute("PRAGMA table_info('parked_contexts')")
        cols = {row[1]: row[3] for row in await cursor.fetchall()}
        assert cols["task_id"] == 0  # notnull=0

    async def test_v7_preserves_existing_data(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        """v7 migration preserves existing parked_contexts rows."""
        await memory_db.execute("""\
CREATE TABLE parked_contexts (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    parked_at TEXT NOT NULL,
    context_json TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
)""")
        await set_user_version(memory_db, 6)

        # Insert a row with NOT NULL task_id
        await memory_db.execute(
            "INSERT INTO parked_contexts "
            "(id, execution_id, agent_id, task_id, approval_id, "
            "parked_at, context_json, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "pc-1",
                "exec-1",
                "agent-1",
                "task-1",
                "approval-1",
                "2026-03-14T10:00:00Z",
                '{"key": "value"}',
                "{}",
            ),
        )
        await memory_db.commit()

        await run_migrations(memory_db)

        cursor = await memory_db.execute("SELECT id, task_id FROM parked_contexts")
        rows = list(await cursor.fetchall())
        assert len(rows) == 1
        assert rows[0][0] == "pc-1"
        assert rows[0][1] == "task-1"
