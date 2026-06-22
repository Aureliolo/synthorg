"""Tests for performance pruning strategy."""

import asyncio

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import TrendDirection
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    TrendResult,
    WindowMetrics,
)
from synthorg.hr.pruning.models import PruningEvaluation
from synthorg.hr.scaling.enums import ScalingActionType
from synthorg.hr.scaling.strategies.performance_pruning import (
    PerformancePruningStrategy,
)

from .conftest import NOW, make_context, make_signal

_AGENT_IDS = ("agent-001", "agent-002")


def _make_snapshot(agent_id: str) -> AgentPerformanceSnapshot:
    return AgentPerformanceSnapshot(
        agent_id=NotBlankStr(agent_id),
        computed_at=NOW,
        windows=(
            WindowMetrics(
                window_size=NotBlankStr("7d"),
                data_point_count=10,
                tasks_completed=8,
                tasks_failed=2,
                currency="EUR",
            ),
        ),
        trends=(
            TrendResult(
                metric_name=NotBlankStr("quality_score"),
                window_size=NotBlankStr("7d"),
                direction=TrendDirection.DECLINING,
                slope=-0.5,
                data_point_count=10,
            ),
        ),
    )


class _StubPruningPolicy:
    """Stub policy that marks all agents as eligible."""

    def __init__(self, *, eligible: bool = True) -> None:
        self._eligible = eligible

    async def evaluate(
        self,
        agent_id: NotBlankStr,
        snapshot: AgentPerformanceSnapshot,
    ) -> PruningEvaluation:
        return PruningEvaluation(
            agent_id=agent_id,
            eligible=self._eligible,
            reasons=(NotBlankStr("below threshold"),) if self._eligible else (),
            scores={"quality": 2.0},
            policy_name=NotBlankStr("stub"),
            snapshot=snapshot,
            evaluated_at=NOW,
        )


@pytest.mark.unit
class TestPerformancePruningStrategy:
    """PerformancePruningStrategy decision logic."""

    @pytest.mark.parametrize(
        ("eligible", "defer_during_evolution", "evolution_active", "expected_count"),
        [
            (True, False, False, 2),
            (False, False, False, 0),
            (True, True, True, 0),
            (True, False, True, 2),
        ],
        ids=[
            "eligible-agents-produce-prune",
            "ineligible-agents-produce-nothing",
            "defers-during-evolution",
            "no-deferral-when-disabled",
        ],
    )
    async def test_eligibility_and_deferral(
        self,
        eligible: bool,
        defer_during_evolution: bool,
        evolution_active: bool,
        expected_count: int,
    ) -> None:
        policy = _StubPruningPolicy(eligible=eligible)

        async def _maybe_adapting(agent_id: str) -> bool:
            return evolution_active

        strategy = PerformancePruningStrategy(
            policy=policy,
            evolution_checker=_maybe_adapting if defer_during_evolution else None,
            defer_during_evolution=defer_during_evolution,
        )
        snapshots = {aid: _make_snapshot(aid) for aid in _AGENT_IDS}
        ctx = make_context(agent_ids=_AGENT_IDS, performance_snapshots=snapshots)
        decisions = await strategy.evaluate(ctx)
        assert len(decisions) == expected_count
        if expected_count > 0:
            assert all(d.action_type == ScalingActionType.PRUNE for d in decisions)

    async def test_no_snapshots_returns_empty(self) -> None:
        policy = _StubPruningPolicy()
        strategy = PerformancePruningStrategy(policy=policy)
        ctx = make_context(agent_ids=_AGENT_IDS)
        decisions = await strategy.evaluate(ctx)
        assert decisions == ()

    async def test_defers_pruning_on_benchmark_regression(self) -> None:
        """A benchmark regression holds the team: no PRUNE decisions."""
        policy = _StubPruningPolicy(eligible=True)
        strategy = PerformancePruningStrategy(policy=policy)
        snapshots = {aid: _make_snapshot(aid) for aid in _AGENT_IDS}
        regression = make_signal(
            name="benchmark_is_regression",
            value=1.0,
            threshold=None,
            source="benchmark",
        )
        ctx = make_context(
            agent_ids=_AGENT_IDS,
            performance_snapshots=snapshots,
            benchmark_signals=(regression,),
        )
        decisions = await strategy.evaluate(ctx)
        assert decisions == ()

    async def test_prunes_when_benchmark_not_regressing(self) -> None:
        """A non-regressing benchmark signal does not block pruning."""
        policy = _StubPruningPolicy(eligible=True)
        strategy = PerformancePruningStrategy(policy=policy)
        snapshots = {aid: _make_snapshot(aid) for aid in _AGENT_IDS}
        no_regression = make_signal(
            name="benchmark_is_regression",
            value=0.0,
            threshold=None,
            source="benchmark",
        )
        ctx = make_context(
            agent_ids=_AGENT_IDS,
            performance_snapshots=snapshots,
            benchmark_signals=(no_regression,),
        )
        decisions = await strategy.evaluate(ctx)
        assert len(decisions) == len(_AGENT_IDS)

    async def test_benchmark_deferral_disabled_prunes_during_regression(self) -> None:
        """With benchmark deferral off, a regression does not block pruning."""
        policy = _StubPruningPolicy(eligible=True)
        strategy = PerformancePruningStrategy(
            policy=policy,
            defer_during_benchmark_regression=False,
        )
        snapshots = {aid: _make_snapshot(aid) for aid in _AGENT_IDS}
        regression = make_signal(
            name="benchmark_is_regression",
            value=1.0,
            threshold=None,
            source="benchmark",
        )
        ctx = make_context(
            agent_ids=_AGENT_IDS,
            performance_snapshots=snapshots,
            benchmark_signals=(regression,),
        )
        decisions = await strategy.evaluate(ctx)
        assert len(decisions) == len(_AGENT_IDS)

    async def test_hung_evolution_checker_does_not_block_fanout(self) -> None:
        """A checker that never returns times out per-agent, not the batch.

        A hung checker is treated as a per-agent error, so the agent yields
        no PRUNE decision and the overall evaluation still completes.
        """
        policy = _StubPruningPolicy(eligible=True)
        never = asyncio.Event()

        async def _hangs(agent_id: str) -> bool:
            await never.wait()
            return True

        strategy = PerformancePruningStrategy(
            policy=policy,
            evolution_checker=_hangs,
            defer_during_evolution=True,
            evolution_check_timeout_seconds=0.01,
        )
        snapshots = {aid: _make_snapshot(aid) for aid in _AGENT_IDS}
        ctx = make_context(agent_ids=_AGENT_IDS, performance_snapshots=snapshots)
        decisions = await strategy.evaluate(ctx)
        assert decisions == ()

    async def test_name_and_action_types(self) -> None:
        policy = _StubPruningPolicy()
        strategy = PerformancePruningStrategy(policy=policy)
        assert strategy.name == "performance_pruning"
        assert ScalingActionType.PRUNE in strategy.action_types
