"""Tests for SQLiteHeartbeatRepository."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from synthorg.engine.checkpoint.models import Heartbeat
from synthorg.persistence.sqlite.heartbeat_repo import (
    SQLiteHeartbeatRepository,
)

if TYPE_CHECKING:
    import aiosqlite

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_heartbeat(
    *,
    execution_id: str = "exec-001",
    agent_id: str = "agent-001",
    task_id: str = "task-001",
    last_heartbeat_at: datetime | None = None,
) -> Heartbeat:
    return Heartbeat(
        execution_id=execution_id,
        agent_id=agent_id,
        task_id=task_id,
        last_heartbeat_at=last_heartbeat_at or datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLiteHeartbeatRepository:
    async def test_save_and_get_roundtrip(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteHeartbeatRepository(migrated_db)
        hb = _make_heartbeat(execution_id="exec-hb-001")
        await repo.save(hb)

        result = await repo.get("exec-hb-001")
        assert result is not None
        assert result.execution_id == hb.execution_id
        assert result.agent_id == hb.agent_id
        assert result.task_id == hb.task_id
        assert result.last_heartbeat_at == hb.last_heartbeat_at

    async def test_get_returns_none_for_missing(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteHeartbeatRepository(migrated_db)
        result = await repo.get("nonexistent")
        assert result is None

    async def test_upsert_updates_existing(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteHeartbeatRepository(migrated_db)
        now = datetime.now(UTC)
        later = now + timedelta(minutes=5)

        hb_original = _make_heartbeat(
            execution_id="exec-upsert",
            last_heartbeat_at=now,
        )
        await repo.save(hb_original)

        hb_updated = _make_heartbeat(
            execution_id="exec-upsert",
            last_heartbeat_at=later,
        )
        await repo.save(hb_updated)

        result = await repo.get("exec-upsert")
        assert result is not None
        assert result.last_heartbeat_at == later

    async def test_get_stale_returns_old_heartbeats(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteHeartbeatRepository(migrated_db)
        now = datetime.now(UTC)
        old = now - timedelta(hours=1)
        very_old = now - timedelta(hours=2)

        hb_fresh = _make_heartbeat(
            execution_id="exec-fresh",
            last_heartbeat_at=now,
        )
        hb_stale = _make_heartbeat(
            execution_id="exec-stale",
            last_heartbeat_at=old,
        )
        hb_very_stale = _make_heartbeat(
            execution_id="exec-very-stale",
            last_heartbeat_at=very_old,
        )
        await repo.save(hb_fresh)
        await repo.save(hb_stale)
        await repo.save(hb_very_stale)

        threshold = now - timedelta(minutes=30)
        stale = await repo.get_stale(threshold)

        stale_ids = {h.execution_id for h in stale}
        assert "exec-stale" in stale_ids
        assert "exec-very-stale" in stale_ids
        assert "exec-fresh" not in stale_ids

    async def test_get_stale_returns_empty_when_none_stale(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteHeartbeatRepository(migrated_db)
        now = datetime.now(UTC)
        hb = _make_heartbeat(
            execution_id="exec-fresh",
            last_heartbeat_at=now,
        )
        await repo.save(hb)

        very_old_threshold = now - timedelta(hours=1)
        stale = await repo.get_stale(very_old_threshold)

        # The heartbeat is newer than the threshold, so not stale
        assert len(stale) == 0

    async def test_get_stale_ordered_by_timestamp(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteHeartbeatRepository(migrated_db)
        now = datetime.now(UTC)
        t1 = now - timedelta(hours=3)
        t2 = now - timedelta(hours=2)

        hb1 = _make_heartbeat(
            execution_id="exec-oldest",
            last_heartbeat_at=t1,
        )
        hb2 = _make_heartbeat(
            execution_id="exec-older",
            last_heartbeat_at=t2,
        )
        # Save in reverse order to verify DB ordering
        await repo.save(hb2)
        await repo.save(hb1)

        threshold = now - timedelta(hours=1)
        stale = await repo.get_stale(threshold)

        assert len(stale) == 2
        assert stale[0].execution_id == "exec-oldest"
        assert stale[1].execution_id == "exec-older"

    async def test_delete_returns_true_when_found(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteHeartbeatRepository(migrated_db)
        hb = _make_heartbeat(execution_id="exec-del")
        await repo.save(hb)

        assert await repo.delete("exec-del") is True
        assert await repo.get("exec-del") is None

    async def test_delete_returns_false_when_not_found(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteHeartbeatRepository(migrated_db)
        assert await repo.delete("nonexistent") is False

    async def test_delete_does_not_affect_other_heartbeats(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteHeartbeatRepository(migrated_db)
        hb_keep = _make_heartbeat(execution_id="exec-keep")
        hb_delete = _make_heartbeat(execution_id="exec-delete")
        await repo.save(hb_keep)
        await repo.save(hb_delete)

        await repo.delete("exec-delete")

        assert await repo.get("exec-keep") is not None
        assert await repo.get("exec-delete") is None


@pytest.mark.unit
class TestSQLiteHeartbeatRepositoryErrors:
    """Error paths raise QueryError."""

    async def test_save_raises_query_error_on_db_error(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        from synthorg.persistence.errors import QueryError

        repo = SQLiteHeartbeatRepository(memory_db)
        hb = _make_heartbeat()
        with pytest.raises(QueryError, match="Failed to save"):
            await repo.save(hb)

    async def test_get_raises_query_error_on_db_error(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        from synthorg.persistence.errors import QueryError

        repo = SQLiteHeartbeatRepository(memory_db)
        with pytest.raises(QueryError, match="Failed to query"):
            await repo.get("exec-001")

    async def test_get_stale_raises_query_error_on_db_error(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        from synthorg.persistence.errors import QueryError

        repo = SQLiteHeartbeatRepository(memory_db)
        threshold = datetime.now(UTC) - timedelta(minutes=5)
        with pytest.raises(QueryError, match="Failed to query"):
            await repo.get_stale(threshold)

    async def test_delete_raises_query_error_on_db_error(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        from synthorg.persistence.errors import QueryError

        repo = SQLiteHeartbeatRepository(memory_db)
        with pytest.raises(QueryError, match="Failed to delete"):
            await repo.delete("exec-001")

    async def test_row_to_model_raises_query_error_on_invalid_row(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        from synthorg.persistence.errors import QueryError

        repo = SQLiteHeartbeatRepository(migrated_db)
        # Insert row with invalid timestamp
        await migrated_db.execute(
            "INSERT INTO heartbeats "
            "(execution_id, agent_id, task_id, last_heartbeat_at) "
            "VALUES (?, ?, ?, ?)",
            ("exec-bad", "agent-bad", "task-bad", "not-a-timestamp"),
        )
        await migrated_db.commit()

        with pytest.raises(QueryError, match="Failed to deserialize"):
            await repo.get("exec-bad")
