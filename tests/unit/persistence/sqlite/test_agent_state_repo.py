"""Tests for SQLiteAgentStateRepository."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from synthorg.core.enums import ExecutionStatus
from synthorg.engine.agent_state import AgentRuntimeState
from synthorg.persistence.sqlite.agent_state_repo import (
    SQLiteAgentStateRepository,
)

if TYPE_CHECKING:
    import aiosqlite

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 3, 15, 11, 0, 0, tzinfo=UTC)
_T2 = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


def _make_state(  # noqa: PLR0913
    *,
    agent_id: str = "agent-001",
    execution_id: str | None = "exec-001",
    task_id: str | None = "task-001",
    status: ExecutionStatus = ExecutionStatus.EXECUTING,
    turn_count: int = 3,
    accumulated_cost: float = 0.05,
    currency: str = "USD",
    last_activity_at: datetime = _T0,
    started_at: datetime | None = _T0,
) -> AgentRuntimeState:
    if status == ExecutionStatus.IDLE:
        return AgentRuntimeState(
            agent_id=agent_id,
            status=ExecutionStatus.IDLE,
            currency=currency,
            last_activity_at=last_activity_at,
        )
    return AgentRuntimeState(
        agent_id=agent_id,
        execution_id=execution_id,
        task_id=task_id,
        status=status,
        turn_count=turn_count,
        accumulated_cost=accumulated_cost,
        currency=currency,
        last_activity_at=last_activity_at,
        started_at=started_at,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLiteAgentStateRepository:
    async def test_save_and_get_roundtrip(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteAgentStateRepository(migrated_db)
        state = _make_state()
        await repo.save(state)

        result = await repo.get("agent-001")
        assert result is not None
        assert result.agent_id == state.agent_id
        assert result.execution_id == state.execution_id
        assert result.task_id == state.task_id
        assert result.status == state.status
        assert result.turn_count == state.turn_count
        assert result.accumulated_cost == state.accumulated_cost
        assert result.currency == state.currency
        assert result.last_activity_at == state.last_activity_at
        assert result.started_at == state.started_at

    async def test_save_idle_roundtrip(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteAgentStateRepository(migrated_db)
        state = _make_state(
            agent_id="agent-idle",
            status=ExecutionStatus.IDLE,
        )
        await repo.save(state)

        result = await repo.get("agent-idle")
        assert result is not None
        assert result.status == ExecutionStatus.IDLE
        assert result.execution_id is None
        assert result.task_id is None
        assert result.started_at is None
        assert result.turn_count == 0
        assert result.accumulated_cost == 0.0
        assert result.currency == "USD"

    async def test_upsert_overwrites(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteAgentStateRepository(migrated_db)
        v1 = _make_state(turn_count=1)
        await repo.save(v1)

        v2 = _make_state(turn_count=5, accumulated_cost=0.10)
        await repo.save(v2)

        result = await repo.get("agent-001")
        assert result is not None
        assert result.turn_count == 5
        assert result.accumulated_cost == pytest.approx(0.10)

    async def test_get_returns_none_when_not_found(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteAgentStateRepository(migrated_db)
        result = await repo.get("nonexistent")
        assert result is None

    async def test_get_active_filters_idle(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteAgentStateRepository(migrated_db)
        executing = _make_state(agent_id="active-1", last_activity_at=_T1)
        paused = _make_state(
            agent_id="paused-1",
            status=ExecutionStatus.PAUSED,
            last_activity_at=_T2,
        )
        idle = _make_state(
            agent_id="idle-1",
            status=ExecutionStatus.IDLE,
            last_activity_at=_T0,
        )
        await repo.save(executing)
        await repo.save(paused)
        await repo.save(idle)

        active = await repo.get_active()
        agent_ids = [s.agent_id for s in active]
        assert "active-1" in agent_ids
        assert "paused-1" in agent_ids
        assert "idle-1" not in agent_ids

    async def test_get_active_ordered_by_last_activity_desc(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteAgentStateRepository(migrated_db)
        older = _make_state(agent_id="older", last_activity_at=_T0)
        newer = _make_state(agent_id="newer", last_activity_at=_T2)
        middle = _make_state(agent_id="middle", last_activity_at=_T1)
        await repo.save(older)
        await repo.save(newer)
        await repo.save(middle)

        active = await repo.get_active()
        assert [s.agent_id for s in active] == ["newer", "middle", "older"]

    async def test_get_active_returns_empty_when_all_idle(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteAgentStateRepository(migrated_db)
        idle = _make_state(agent_id="idle-only", status=ExecutionStatus.IDLE)
        await repo.save(idle)

        active = await repo.get_active()
        assert active == ()

    async def test_delete_existing(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteAgentStateRepository(migrated_db)
        state = _make_state()
        await repo.save(state)

        deleted = await repo.delete("agent-001")
        assert deleted is True
        assert await repo.get("agent-001") is None

    async def test_delete_nonexistent(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteAgentStateRepository(migrated_db)
        deleted = await repo.delete("nonexistent")
        assert deleted is False

    async def test_delete_does_not_affect_other_agents(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteAgentStateRepository(migrated_db)
        keep = _make_state(agent_id="keep")
        remove = _make_state(agent_id="remove")
        await repo.save(keep)
        await repo.save(remove)

        await repo.delete("remove")
        assert await repo.get("keep") is not None
        assert await repo.get("remove") is None

    async def test_get_active_returns_empty_on_empty_table(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteAgentStateRepository(migrated_db)
        active = await repo.get_active()
        assert active == ()

    async def test_lifecycle_idle_to_executing_to_idle(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Full lifecycle: idle → executing → idle roundtrip."""
        repo = SQLiteAgentStateRepository(migrated_db)
        idle = _make_state(agent_id="lifecycle", status=ExecutionStatus.IDLE)
        await repo.save(idle)

        result = await repo.get("lifecycle")
        assert result is not None
        assert result.status == ExecutionStatus.IDLE

        executing = _make_state(
            agent_id="lifecycle",
            status=ExecutionStatus.EXECUTING,
            last_activity_at=_T1,
        )
        await repo.save(executing)

        result = await repo.get("lifecycle")
        assert result is not None
        assert result.status == ExecutionStatus.EXECUTING
        assert result.execution_id == "exec-001"
        assert result.started_at == _T0

        idle_again = _make_state(
            agent_id="lifecycle",
            status=ExecutionStatus.IDLE,
            last_activity_at=_T2,
        )
        await repo.save(idle_again)

        result = await repo.get("lifecycle")
        assert result is not None
        assert result.status == ExecutionStatus.IDLE
        assert result.execution_id is None
        assert result.task_id is None
        assert result.started_at is None
        assert result.turn_count == 0
        assert result.accumulated_cost == 0.0


@pytest.mark.unit
class TestSQLiteAgentStateRepositoryErrors:
    """Error paths raise QueryError."""

    @pytest.mark.parametrize(
        ("method", "args", "match"),
        [
            ("save", (_make_state(),), "Failed to save"),
            ("get", ("agent-001",), "Failed to fetch"),
            ("get_active", (), "Failed to query"),
            ("delete", ("agent-001",), "Failed to delete"),
        ],
    )
    async def test_crud_raises_query_error_on_db_error(
        self,
        memory_db: aiosqlite.Connection,
        method: str,
        args: tuple[object, ...],
        match: str,
    ) -> None:
        from synthorg.core.persistence_errors import QueryError

        repo = SQLiteAgentStateRepository(memory_db)
        with pytest.raises(QueryError, match=match) as exc_info:
            await getattr(repo, method)(*args)
        assert exc_info.value.__cause__ is not None

    async def test_row_to_model_raises_query_error_on_invalid_row(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        from synthorg.core.persistence_errors import QueryError

        repo = SQLiteAgentStateRepository(migrated_db)
        # Insert a row with a malformed datetime to trigger deserialization
        # failure (passes CHECK constraints but fails Pydantic AwareDatetime)
        await migrated_db.execute(
            "INSERT INTO agent_states "
            "(agent_id, execution_id, task_id, status, turn_count, "
            "accumulated_cost, last_activity_at, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "agent-bad",
                "exec-bad",
                None,
                "executing",
                0,
                0.0,
                "not-a-datetime",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        await migrated_db.commit()

        with pytest.raises(QueryError, match="Failed to deserialize"):
            await repo.get("agent-bad")

    async def test_get_active_raises_query_error_on_corrupt_row(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """get_active() fails when any row has corrupt data."""
        from synthorg.core.persistence_errors import QueryError

        repo = SQLiteAgentStateRepository(migrated_db)
        # Insert a valid executing row
        valid = _make_state(agent_id="agent-ok")
        await repo.save(valid)
        # Insert a corrupt row with a malformed datetime (passes CHECK
        # constraints but fails Pydantic AwareDatetime validation)
        await migrated_db.execute(
            "INSERT INTO agent_states "
            "(agent_id, execution_id, task_id, status, turn_count, "
            "accumulated_cost, last_activity_at, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "agent-corrupt",
                "exec-corrupt",
                None,
                "executing",
                0,
                0.0,
                "not-a-datetime",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        await migrated_db.commit()

        with pytest.raises(QueryError, match="Failed to deserialize"):
            await repo.get_active()
