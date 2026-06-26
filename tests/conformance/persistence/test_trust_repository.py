"""Conformance tests for the trust state + change history repositories."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.trust_state_protocol import (
    TrustChangeHistoryFilterSpec,
)
from synthorg.security.trust.enums import TrustChangeReason
from synthorg.security.trust.models import TrustChangeRecord, TrustState

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC)


def _state(
    *,
    agent_id: str = "agent-alpha",
    level: ToolAccessLevel = ToolAccessLevel.SANDBOXED,
    score: float | None = None,
) -> TrustState:
    return TrustState(
        agent_id=NotBlankStr(agent_id),
        global_level=level,
        created_at=_NOW,
        category_levels={"filesystem": ToolAccessLevel.STANDARD},
        trust_score=score,
        last_evaluated_at=_NOW,
        milestone_progress={"tasks_completed": 7, "score_avg": 0.92},
    )


def _record(
    *,
    agent_id: str = "agent-alpha",
    new_level: ToolAccessLevel = ToolAccessLevel.STANDARD,
    when: datetime = _NOW,
    record_id: str | None = None,
) -> TrustChangeRecord:
    return TrustChangeRecord(
        id=NotBlankStr(record_id or f"chg-{agent_id}-{when.isoformat()}"),
        agent_id=NotBlankStr(agent_id),
        old_level=ToolAccessLevel.SANDBOXED,
        new_level=new_level,
        reason=TrustChangeReason.SCORE_THRESHOLD,
        timestamp=when,
        details="promoted on score threshold",
    )


class TestTrustStateRepository:
    async def test_save_and_get_round_trip(self, backend: PersistenceBackend) -> None:
        await backend.trust_states.save(_state(score=0.75))

        result = await backend.trust_states.get(NotBlankStr("agent-alpha"))
        assert result is not None
        assert result.agent_id == "agent-alpha"
        assert result.global_level is ToolAccessLevel.SANDBOXED
        assert result.category_levels == {"filesystem": ToolAccessLevel.STANDARD}
        assert result.trust_score == pytest.approx(0.75)
        assert result.milestone_progress == {
            "tasks_completed": 7,
            "score_avg": 0.92,
        }
        assert result.created_at == _NOW

    async def test_save_upserts(self, backend: PersistenceBackend) -> None:
        await backend.trust_states.save(_state(level=ToolAccessLevel.SANDBOXED))
        await backend.trust_states.save(_state(level=ToolAccessLevel.ELEVATED))

        result = await backend.trust_states.get(NotBlankStr("agent-alpha"))
        assert result is not None
        assert result.global_level is ToolAccessLevel.ELEVATED

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.trust_states.get(NotBlankStr("ghost")) is None

    async def test_delete(self, backend: PersistenceBackend) -> None:
        await backend.trust_states.save(_state())
        assert await backend.trust_states.delete(NotBlankStr("agent-alpha"))
        assert await backend.trust_states.get(NotBlankStr("agent-alpha")) is None
        assert not await backend.trust_states.delete(NotBlankStr("agent-alpha"))

    async def test_list_items_key_order(self, backend: PersistenceBackend) -> None:
        await backend.trust_states.save(_state(agent_id="ts-zeta"))
        await backend.trust_states.save(_state(agent_id="ts-alpha"))

        results = await backend.trust_states.list_items()
        scoped = [r.agent_id for r in results if r.agent_id.startswith("ts-")]
        assert scoped == ["ts-alpha", "ts-zeta"]

    async def test_null_score_round_trips(self, backend: PersistenceBackend) -> None:
        await backend.trust_states.save(_state(score=None))
        result = await backend.trust_states.get(NotBlankStr("agent-alpha"))
        assert result is not None
        assert result.trust_score is None


class TestTrustChangeHistoryRepository:
    async def test_append_and_query_newest_first(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.trust_change_history.append(_record(when=_NOW, record_id="r1"))
        await backend.trust_change_history.append(
            _record(
                when=_NOW + timedelta(hours=1),
                new_level=ToolAccessLevel.ELEVATED,
                record_id="r2",
            )
        )

        results = await backend.trust_change_history.query(
            TrustChangeHistoryFilterSpec(agent_id=NotBlankStr("agent-alpha"))
        )
        assert [r.id for r in results] == ["r2", "r1"]

    async def test_query_filters_by_agent(self, backend: PersistenceBackend) -> None:
        await backend.trust_change_history.append(
            _record(agent_id="agent-alpha", record_id="a1")
        )
        await backend.trust_change_history.append(
            _record(agent_id="agent-beta", record_id="b1")
        )

        alpha = await backend.trust_change_history.query(
            TrustChangeHistoryFilterSpec(agent_id=NotBlankStr("agent-alpha"))
        )
        assert {r.agent_id for r in alpha} == {"agent-alpha"}

    async def test_query_all_agents(self, backend: PersistenceBackend) -> None:
        await backend.trust_change_history.append(
            _record(agent_id="agent-alpha", record_id="a1")
        )
        await backend.trust_change_history.append(
            _record(agent_id="agent-beta", record_id="b1")
        )

        every = await backend.trust_change_history.query(TrustChangeHistoryFilterSpec())
        assert {"a1", "b1"} <= {r.id for r in every}

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        old = _NOW - timedelta(days=30)
        await backend.trust_change_history.append(_record(when=old, record_id="old"))
        await backend.trust_change_history.append(_record(when=_NOW, record_id="new"))

        removed = await backend.trust_change_history.purge_before(
            _NOW - timedelta(days=1)
        )
        assert removed == 1
        remaining = await backend.trust_change_history.query(
            TrustChangeHistoryFilterSpec(agent_id=NotBlankStr("agent-alpha"))
        )
        assert [r.id for r in remaining] == ["new"]
