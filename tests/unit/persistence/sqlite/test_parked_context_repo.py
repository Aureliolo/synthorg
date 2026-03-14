"""Tests for SQLiteParkedContextRepository."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from synthorg.persistence.errors import QueryError
from synthorg.persistence.sqlite.parked_context_repo import (
    SQLiteParkedContextRepository,
)
from synthorg.security.timeout.parked_context import ParkedContext

if TYPE_CHECKING:
    import aiosqlite


def _make_context(  # noqa: PLR0913
    *,
    parked_id: str | None = None,
    execution_id: str = "exec-001",
    agent_id: str = "agent-001",
    task_id: str = "task-001",
    approval_id: str = "approval-001",
    parked_at: datetime | None = None,
    context_json: str = '{"state": "running"}',
    metadata: dict[str, str] | None = None,
) -> ParkedContext:
    return ParkedContext(
        id=parked_id or str(uuid4()),
        execution_id=execution_id,
        agent_id=agent_id,
        task_id=task_id,
        approval_id=approval_id,
        parked_at=parked_at or datetime.now(UTC),
        context_json=context_json,
        metadata=metadata or {},
    )


@pytest.mark.unit
class TestSQLiteParkedContextRepository:
    async def test_save_and_get(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteParkedContextRepository(migrated_db)
        ctx = _make_context(parked_id="parked-001")
        await repo.save(ctx)

        result = await repo.get("parked-001")
        assert result is not None
        assert result.id == ctx.id
        assert result.execution_id == ctx.execution_id
        assert result.agent_id == ctx.agent_id
        assert result.task_id == ctx.task_id
        assert result.approval_id == ctx.approval_id
        assert result.parked_at == ctx.parked_at
        assert result.context_json == ctx.context_json
        assert result.metadata == ctx.metadata

    async def test_get_returns_none_for_missing(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteParkedContextRepository(migrated_db)
        assert await repo.get("nonexistent") is None

    async def test_get_by_approval(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteParkedContextRepository(migrated_db)
        ctx = _make_context(approval_id="approval-xyz")
        await repo.save(ctx)

        result = await repo.get_by_approval("approval-xyz")
        assert result is not None
        assert result.approval_id == "approval-xyz"
        assert result.id == ctx.id

    async def test_get_by_approval_returns_none(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteParkedContextRepository(migrated_db)
        assert await repo.get_by_approval("nonexistent") is None

    async def test_get_by_agent(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteParkedContextRepository(migrated_db)
        ctx1 = _make_context(agent_id="agent-a", approval_id="ap-1")
        ctx2 = _make_context(agent_id="agent-a", approval_id="ap-2")
        await repo.save(ctx1)
        await repo.save(ctx2)

        results = await repo.get_by_agent("agent-a")
        assert len(results) == 2
        ids = {r.id for r in results}
        assert ctx1.id in ids
        assert ctx2.id in ids

    async def test_get_by_agent_returns_empty(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteParkedContextRepository(migrated_db)
        results = await repo.get_by_agent("nonexistent")
        assert results == ()

    async def test_get_by_agent_ordered_by_parked_at_desc(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteParkedContextRepository(migrated_db)
        now = datetime.now(UTC)
        earlier = now - timedelta(hours=1)

        ctx_old = _make_context(
            agent_id="agent-b",
            approval_id="ap-old",
            parked_at=earlier,
        )
        ctx_new = _make_context(
            agent_id="agent-b",
            approval_id="ap-new",
            parked_at=now,
        )
        # Save in chronological order to ensure DB ordering is not
        # merely insertion order.
        await repo.save(ctx_old)
        await repo.save(ctx_new)

        results = await repo.get_by_agent("agent-b")
        assert len(results) == 2
        assert results[0].id == ctx_new.id
        assert results[1].id == ctx_old.id

    async def test_delete(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteParkedContextRepository(migrated_db)
        ctx = _make_context(parked_id="del-me")
        await repo.save(ctx)

        assert await repo.delete("del-me") is True
        assert await repo.get("del-me") is None

    async def test_delete_returns_false_for_missing(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        repo = SQLiteParkedContextRepository(migrated_db)
        assert await repo.delete("nonexistent") is False

    async def test_save_upsert(self, migrated_db: aiosqlite.Connection) -> None:
        repo = SQLiteParkedContextRepository(migrated_db)
        ctx = _make_context(
            parked_id="upsert-id",
            context_json='{"step": 1}',
            metadata={"key": "original"},
        )
        await repo.save(ctx)

        updated = ParkedContext(
            id="upsert-id",
            execution_id=ctx.execution_id,
            agent_id=ctx.agent_id,
            task_id=ctx.task_id,
            approval_id=ctx.approval_id,
            parked_at=ctx.parked_at,
            context_json='{"step": 2}',
            metadata={"key": "updated"},
        )
        await repo.save(updated)

        result = await repo.get("upsert-id")
        assert result is not None
        assert result.context_json == '{"step": 2}'
        assert result.metadata == {"key": "updated"}

    async def test_save_round_trips_metadata(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Metadata dict survives JSON serialization round-trip."""
        repo = SQLiteParkedContextRepository(migrated_db)
        ctx = _make_context(
            parked_id="meta-rt",
            metadata={"tool": "shell", "action": "execute"},
        )
        await repo.save(ctx)

        result = await repo.get("meta-rt")
        assert result is not None
        assert result.metadata == {"tool": "shell", "action": "execute"}

    async def test_row_to_model_raises_on_corrupt_data(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Corrupt metadata JSON triggers QueryError in _row_to_model."""
        await migrated_db.execute(
            """\
INSERT INTO parked_contexts (
    id, execution_id, agent_id, task_id, approval_id,
    parked_at, context_json, metadata
) VALUES (
    'corrupt-1', 'exec-1', 'agent-1', 'task-1', 'approval-1',
    '2026-03-01T12:00:00+00:00', '{}', '{BAD JSON}'
)"""
        )
        await migrated_db.commit()

        repo = SQLiteParkedContextRepository(migrated_db)
        with pytest.raises(QueryError, match="deserialize parked context"):
            await repo.get("corrupt-1")
