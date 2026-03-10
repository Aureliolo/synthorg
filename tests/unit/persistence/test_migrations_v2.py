"""Tests for v2 schema migration (HR persistence tables)."""

from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
import pytest

from ai_company.persistence.sqlite.migrations import (
    SCHEMA_VERSION,
    _apply_v1,
    get_user_version,
    run_migrations,
    set_user_version,
)


@pytest.fixture
async def memory_db() -> AsyncGenerator[aiosqlite.Connection]:
    """Raw in-memory SQLite connection (no migrations)."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    yield conn
    await conn.close()


@pytest.mark.unit
class TestV2Migration:
    async def test_schema_version_is_three(self) -> None:
        assert SCHEMA_VERSION == 3

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
        assert await get_user_version(memory_db) == 3

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
