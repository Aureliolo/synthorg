"""Tests for SQLiteCheckpointRepository."""

from typing import TYPE_CHECKING

import pytest

from synthorg.engine.checkpoint.models import Checkpoint
from synthorg.persistence.sqlite.checkpoint_repo import (
    SQLiteCheckpointRepository,
)

if TYPE_CHECKING:
    import aiosqlite

pytestmark = pytest.mark.timeout(30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_checkpoint(  # noqa: PLR0913
    *,
    checkpoint_id: str = "cp-001",
    execution_id: str = "exec-001",
    agent_id: str = "agent-001",
    task_id: str = "task-001",
    turn_number: int = 1,
    context_json: str = '{"state": "running"}',
) -> Checkpoint:
    return Checkpoint(
        id=checkpoint_id,
        execution_id=execution_id,
        agent_id=agent_id,
        task_id=task_id,
        turn_number=turn_number,
        context_json=context_json,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLiteCheckpointRepository:
    async def test_save_and_get_latest_roundtrip(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteCheckpointRepository(migrated_db)
        cp = _make_checkpoint(checkpoint_id="cp-rt-001")
        await repo.save(cp)

        result = await repo.get_latest(execution_id="exec-001")
        assert result is not None
        assert result.id == cp.id
        assert result.execution_id == cp.execution_id
        assert result.agent_id == cp.agent_id
        assert result.task_id == cp.task_id
        assert result.turn_number == cp.turn_number
        assert result.context_json == cp.context_json
        assert result.created_at == cp.created_at

    async def test_get_latest_returns_highest_turn_number(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteCheckpointRepository(migrated_db)
        cp_low = _make_checkpoint(
            checkpoint_id="cp-low",
            turn_number=1,
        )
        cp_high = _make_checkpoint(
            checkpoint_id="cp-high",
            turn_number=5,
        )
        cp_mid = _make_checkpoint(
            checkpoint_id="cp-mid",
            turn_number=3,
        )
        # Insert in non-order to confirm DB ordering
        await repo.save(cp_mid)
        await repo.save(cp_low)
        await repo.save(cp_high)

        result = await repo.get_latest(execution_id="exec-001")
        assert result is not None
        assert result.id == "cp-high"
        assert result.turn_number == 5

    async def test_get_latest_filter_by_task_id(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteCheckpointRepository(migrated_db)
        cp_a = _make_checkpoint(
            checkpoint_id="cp-a",
            task_id="task-alpha",
            turn_number=3,
        )
        cp_b = _make_checkpoint(
            checkpoint_id="cp-b",
            task_id="task-beta",
            turn_number=5,
        )
        await repo.save(cp_a)
        await repo.save(cp_b)

        result = await repo.get_latest(task_id="task-alpha")
        assert result is not None
        assert result.id == "cp-a"
        assert result.task_id == "task-alpha"

    async def test_get_latest_filter_by_execution_id(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteCheckpointRepository(migrated_db)
        cp_a = _make_checkpoint(
            checkpoint_id="cp-exec-a",
            execution_id="exec-alpha",
            turn_number=2,
        )
        cp_b = _make_checkpoint(
            checkpoint_id="cp-exec-b",
            execution_id="exec-beta",
            turn_number=4,
        )
        await repo.save(cp_a)
        await repo.save(cp_b)

        result = await repo.get_latest(execution_id="exec-alpha")
        assert result is not None
        assert result.id == "cp-exec-a"
        assert result.execution_id == "exec-alpha"

    async def test_get_latest_both_filters(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteCheckpointRepository(migrated_db)
        cp_match = _make_checkpoint(
            checkpoint_id="cp-match",
            execution_id="exec-m",
            task_id="task-m",
            turn_number=3,
        )
        cp_exec_only = _make_checkpoint(
            checkpoint_id="cp-exec-only",
            execution_id="exec-m",
            task_id="task-other",
            turn_number=5,
        )
        await repo.save(cp_match)
        await repo.save(cp_exec_only)

        result = await repo.get_latest(execution_id="exec-m", task_id="task-m")
        assert result is not None
        assert result.id == "cp-match"

    async def test_get_latest_returns_none_when_no_match(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteCheckpointRepository(migrated_db)
        result = await repo.get_latest(execution_id="nonexistent")
        assert result is None

    async def test_get_latest_raises_when_no_filter(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteCheckpointRepository(migrated_db)
        with pytest.raises(ValueError, match="At least one"):
            await repo.get_latest()

    async def test_upsert_same_id(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteCheckpointRepository(migrated_db)
        cp_v1 = _make_checkpoint(
            checkpoint_id="cp-upsert",
            context_json='{"version": 1}',
            turn_number=1,
        )
        await repo.save(cp_v1)

        cp_v2 = _make_checkpoint(
            checkpoint_id="cp-upsert",
            context_json='{"version": 2}',
            turn_number=2,
        )
        await repo.save(cp_v2)

        result = await repo.get_latest(execution_id="exec-001")
        assert result is not None
        assert result.id == "cp-upsert"
        assert result.context_json == '{"version": 2}'
        assert result.turn_number == 2

    async def test_delete_by_execution_returns_count(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteCheckpointRepository(migrated_db)
        for i in range(3):
            cp = _make_checkpoint(
                checkpoint_id=f"cp-del-{i}",
                execution_id="exec-to-delete",
                turn_number=i,
            )
            await repo.save(cp)

        count = await repo.delete_by_execution("exec-to-delete")
        assert count == 3

        result = await repo.get_latest(execution_id="exec-to-delete")
        assert result is None

    async def test_delete_by_execution_returns_zero_when_none_exist(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteCheckpointRepository(migrated_db)
        count = await repo.delete_by_execution("nonexistent")
        assert count == 0

    async def test_delete_by_execution_does_not_affect_other_executions(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteCheckpointRepository(migrated_db)
        cp_keep = _make_checkpoint(
            checkpoint_id="cp-keep",
            execution_id="exec-keep",
        )
        cp_delete = _make_checkpoint(
            checkpoint_id="cp-delete",
            execution_id="exec-delete",
        )
        await repo.save(cp_keep)
        await repo.save(cp_delete)

        await repo.delete_by_execution("exec-delete")

        assert await repo.get_latest(execution_id="exec-keep") is not None
        assert await repo.get_latest(execution_id="exec-delete") is None


@pytest.mark.unit
class TestSQLiteCheckpointRepositoryErrors:
    """Error paths raise QueryError."""

    async def test_save_raises_query_error_on_db_error(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        """save() wraps sqlite errors into QueryError."""
        from synthorg.persistence.errors import QueryError

        # No migrations → table doesn't exist → sqlite error
        repo = SQLiteCheckpointRepository(memory_db)
        cp = _make_checkpoint()
        with pytest.raises(QueryError, match="Failed to save"):
            await repo.save(cp)

    async def test_get_latest_raises_query_error_on_db_error(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        """get_latest() wraps sqlite errors into QueryError."""
        from synthorg.persistence.errors import QueryError

        repo = SQLiteCheckpointRepository(memory_db)
        with pytest.raises(QueryError, match="Failed to query"):
            await repo.get_latest(execution_id="exec-001")

    async def test_delete_raises_query_error_on_db_error(
        self, memory_db: aiosqlite.Connection
    ) -> None:
        """delete_by_execution() wraps sqlite errors into QueryError."""
        from synthorg.persistence.errors import QueryError

        repo = SQLiteCheckpointRepository(memory_db)
        with pytest.raises(QueryError, match="Failed to delete"):
            await repo.delete_by_execution("exec-001")

    async def test_row_to_model_raises_query_error_on_invalid_row(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """_row_to_model() wraps ValidationError into QueryError."""
        from synthorg.persistence.errors import QueryError

        repo = SQLiteCheckpointRepository(migrated_db)
        # Manually insert a row with invalid data (missing required fields)
        await migrated_db.execute(
            "INSERT INTO checkpoints "
            "(id, execution_id, agent_id, task_id, turn_number, "
            "context_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "cp-bad",
                "exec-bad",
                "agent-bad",
                "task-bad",
                1,
                "not-valid-json",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        await migrated_db.commit()

        with pytest.raises(QueryError, match="Failed to deserialize"):
            await repo.get_latest(execution_id="exec-bad")
