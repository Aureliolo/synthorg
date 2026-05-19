"""Property-based tests for CoordinationMetricsCollector."""

from unittest.mock import MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.budget.baseline_store import BaselineRecord, BaselineStore
from synthorg.budget.coordination_collector import (
    CollectionInputs,
    CoordinationMetricsCollector,
)
from synthorg.budget.coordination_config import CoordinationMetricsConfig
from synthorg.budget.coordination_metrics import CoordinationMetrics
from synthorg.budget.tracker import CostTracker
from synthorg.engine.loop_protocol import ExecutionResult
from synthorg.providers.enums import FinishReason

# ---------------------------------------------------------------------------
# Helpers (duplicated here to avoid test-file dependencies)
# ---------------------------------------------------------------------------


def _cfg(enabled: bool = True) -> CoordinationMetricsConfig:
    return CoordinationMetricsConfig(enabled=enabled)


def _turn(finish_reason: FinishReason = FinishReason.STOP) -> MagicMock:
    turn = MagicMock()
    turn.finish_reason = finish_reason
    turn.total_tokens = 100
    turn.latency_ms = 50.0
    return turn


def _execution_result(*turns: MagicMock) -> MagicMock:
    result = MagicMock()
    result.turns = turns
    return result


async def _collect(  # noqa: PLR0913
    collector: CoordinationMetricsCollector,
    *,
    execution_result: ExecutionResult,
    agent_id: str,
    task_id: str,
    team_size: int = 1,
    agent_durations: tuple[tuple[str, float], ...] | None = None,
    agent_outputs: tuple[str, ...] | None = None,
    is_multi_agent: bool = False,
) -> CoordinationMetrics:
    """Invoke ``collect`` with the kwargs bundled into ``CollectionInputs``."""
    return await collector.collect(
        CollectionInputs(
            execution_result=execution_result,
            agent_id=agent_id,
            task_id=task_id,
            team_size=team_size,
            agent_durations=agent_durations,
            agent_outputs=agent_outputs,
            is_multi_agent=is_multi_agent,
        ),
    )


def _baseline_store_with_record(turns: float = 5.0) -> BaselineStore:
    store = BaselineStore(window_size=50)
    store.record(
        BaselineRecord(
            agent_id="sas-agent",
            task_id="t",
            turns=turns,
            error_rate=0.1,
            total_tokens=1000.0,
            duration_seconds=10.0,
        )
    )
    return store


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCoordinationCollectorProperties:
    """Invariants that must hold for all valid inputs."""

    @given(st.booleans())
    async def test_collect_always_returns_coordination_metrics(
        self, is_multi: bool
    ) -> None:
        """collect() always returns a CoordinationMetrics instance."""
        collector = CoordinationMetricsCollector(
            config=_cfg(),
            cost_tracker=MagicMock(spec=CostTracker),
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="agent-1",
            task_id="task-1",
            is_multi_agent=is_multi,
        )
        assert isinstance(result, CoordinationMetrics)

    @given(st.booleans())
    async def test_disabled_always_returns_empty(self, is_multi: bool) -> None:
        """When config.enabled=False, all metric fields are None."""
        collector = CoordinationMetricsCollector(
            config=_cfg(enabled=False),
            cost_tracker=MagicMock(spec=CostTracker),
        )
        result = await _collect(
            collector,
            execution_result=_execution_result(_turn()),
            agent_id="agent-1",
            task_id="task-1",
            is_multi_agent=is_multi,
        )
        assert result.efficiency is None
        assert result.overhead is None
        assert result.error_amplification is None
        assert result.message_density is None
        assert result.redundancy_rate is None
        assert result.amdahl_ceiling is None
        assert result.straggler_gap is None
        assert result.token_speedup_ratio is None
        assert result.message_overhead is None

    @given(st.integers(min_value=1, max_value=20))
    async def test_single_agent_records_one_baseline_per_call(
        self, n_calls: int
    ) -> None:
        """Each single-agent collect() call adds exactly one baseline record."""
        store = BaselineStore(window_size=100)
        collector = CoordinationMetricsCollector(
            config=_cfg(),
            cost_tracker=MagicMock(spec=CostTracker),
            baseline_store=store,
        )
        for i in range(n_calls):
            await _collect(
                collector,
                execution_result=_execution_result(_turn()),
                agent_id=f"agent-{i}",
                task_id="task-1",
                is_multi_agent=False,
            )
        assert len(store) == n_calls

    @given(
        st.integers(min_value=1, max_value=100),
        st.integers(min_value=1, max_value=100),
    )
    async def test_overhead_sign_consistent_with_turns(
        self, turns_mas: int, turns_sas: int
    ) -> None:
        """overhead.value_percent >= 0 iff turns_mas >= turns_sas."""
        store = _baseline_store_with_record(turns=float(turns_sas))
        collector = CoordinationMetricsCollector(
            config=CoordinationMetricsConfig(enabled=True),
            cost_tracker=MagicMock(spec=CostTracker),
            baseline_store=store,
        )
        turns = tuple(_turn() for _ in range(turns_mas))
        result = await _collect(
            collector,
            execution_result=_execution_result(*turns),
            agent_id="a",
            task_id="t",
            is_multi_agent=True,
        )
        if result.overhead is not None:
            if turns_mas >= turns_sas:
                assert result.overhead.value_percent >= 0
            else:
                assert result.overhead.value_percent <= 0
