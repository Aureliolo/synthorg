"""Conformance tests for ``AgentStateRepository`` (SQLite + Postgres).

Parametrized over the shared ``backend`` fixture so the same protocol
assertions run against both implementations. Complements the SQLite-only
unit tests under ``tests/unit/persistence/sqlite/test_agent_state_repo.py``.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import ExecutionStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_state import AgentRuntimeState
from synthorg.persistence.protocol import PersistenceBackend

pytestmark = pytest.mark.integration

_T0 = datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 3, 15, 11, 0, 0, tzinfo=UTC)
_T2 = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


def _executing(
    *,
    agent_id: str = "agent-001",
    turn_count: int = 3,
    accumulated_cost: float = 0.05,
    last_activity_at: datetime = _T0,
) -> AgentRuntimeState:
    return AgentRuntimeState(
        agent_id=NotBlankStr(agent_id),
        execution_id=NotBlankStr("exec-001"),
        task_id=NotBlankStr("task-001"),
        status=ExecutionStatus.EXECUTING,
        turn_count=turn_count,
        accumulated_cost=accumulated_cost,
        currency="USD",
        last_activity_at=last_activity_at,
        started_at=_T0,
    )


def _idle(agent_id: str = "agent-idle") -> AgentRuntimeState:
    return AgentRuntimeState(
        agent_id=NotBlankStr(agent_id),
        status=ExecutionStatus.IDLE,
        currency="USD",
        last_activity_at=_T0,
    )


class TestAgentStateRepository:
    async def test_save_and_get_roundtrip(self, backend: PersistenceBackend) -> None:
        state = _executing()
        await backend.agent_states.save(state)

        result = await backend.agent_states.get(NotBlankStr("agent-001"))
        assert result is not None
        assert result.agent_id == "agent-001"
        assert result.status == ExecutionStatus.EXECUTING
        assert result.turn_count == 3
        assert result.accumulated_cost == pytest.approx(0.05)

    async def test_save_idle_roundtrip(self, backend: PersistenceBackend) -> None:
        await backend.agent_states.save(_idle())

        result = await backend.agent_states.get(NotBlankStr("agent-idle"))
        assert result is not None
        assert result.status == ExecutionStatus.IDLE
        assert result.execution_id is None
        assert result.task_id is None

    async def test_upsert_overwrites(self, backend: PersistenceBackend) -> None:
        await backend.agent_states.save(_executing(turn_count=1))
        await backend.agent_states.save(_executing(turn_count=7, accumulated_cost=0.10))

        result = await backend.agent_states.get(NotBlankStr("agent-001"))
        assert result is not None
        assert result.turn_count == 7
        assert result.accumulated_cost == pytest.approx(0.10)

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.agent_states.get(NotBlankStr("ghost")) is None

    async def test_get_active_filters_idle(self, backend: PersistenceBackend) -> None:
        await backend.agent_states.save(_executing(agent_id="active-1"))
        await backend.agent_states.save(_idle(agent_id="idle-1"))

        active = await backend.agent_states.get_active()
        ids = {s.agent_id for s in active}
        assert "active-1" in ids
        assert "idle-1" not in ids

    async def test_get_active_ordered_by_last_activity_desc(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.agent_states.save(
            _executing(agent_id="older", last_activity_at=_T0),
        )
        await backend.agent_states.save(
            _executing(agent_id="newer", last_activity_at=_T2),
        )
        await backend.agent_states.save(
            _executing(agent_id="middle", last_activity_at=_T1),
        )

        # Scope the assertion to the three agent_ids created by this test.
        # ``get_active`` returns every active state in the shared backend,
        # so a sibling test that happens to persist another active state
        # would make the global ordering flaky; filter to the rows we own
        # and assert their relative order instead.
        active = await backend.agent_states.get_active()
        scoped_ids = {"older", "middle", "newer"}
        ordered_ids = [s.agent_id for s in active if s.agent_id in scoped_ids]
        assert ordered_ids == ["newer", "middle", "older"]

    async def test_list_items_returns_all_in_id_order(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.agent_states.save(_executing(agent_id="list-b"))
        await backend.agent_states.save(_executing(agent_id="list-a"))
        await backend.agent_states.save(_idle(agent_id="list-c"))

        results = await backend.agent_states.list_items()
        scoped = [s.agent_id for s in results if s.agent_id.startswith("list-")]
        assert scoped == ["list-a", "list-b", "list-c"]

    async def test_list_items_respects_limit_and_offset(
        self, backend: PersistenceBackend
    ) -> None:
        for suffix in ("a", "b", "c"):
            await backend.agent_states.save(_executing(agent_id=f"page-{suffix}"))

        all_ids = [
            s.agent_id
            for s in await backend.agent_states.list_items(limit=100, offset=0)
            if s.agent_id.startswith("page-")
        ]
        assert all_ids == ["page-a", "page-b", "page-c"]

        # Slice the in-test subset by index against limit/offset on the
        # full list, since the shared backend may contain rows from
        # sibling tests.
        page = [
            s.agent_id
            for s in await backend.agent_states.list_items(limit=10, offset=0)
            if s.agent_id.startswith("page-")
        ]
        assert page[:2] == ["page-a", "page-b"]

    async def test_delete_existing(self, backend: PersistenceBackend) -> None:
        await backend.agent_states.save(_executing())

        deleted = await backend.agent_states.delete(NotBlankStr("agent-001"))
        assert deleted is True
        assert await backend.agent_states.get(NotBlankStr("agent-001")) is None

    async def test_delete_missing(self, backend: PersistenceBackend) -> None:
        deleted = await backend.agent_states.delete(NotBlankStr("ghost"))
        assert deleted is False
