"""Tests for scaling service orchestrator."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.config import ScalingConfig
from synthorg.hr.scaling.context import ScalingContextBuilder
from synthorg.hr.scaling.enums import (
    ScalingActionType,
    ScalingOutcome,
    ScalingStrategyName,
)
from synthorg.hr.scaling.guards.composite import CompositeScalingGuard
from synthorg.hr.scaling.guards.rate_limit import RateLimitGuard
from synthorg.hr.scaling.models import (
    ScalingActionRecord,
    ScalingDecision,
    ScalingSignal,
)
from synthorg.hr.scaling.service import ScalingService
from synthorg.hr.scaling.triggers.batched import BatchedScalingTrigger
from synthorg.hr.scaling.triggers.threshold import SignalThresholdTrigger

from .conftest import NOW, make_decision


def _utilization_signal(value: float) -> ScalingSignal:
    return ScalingSignal(
        name=NotBlankStr("avg_utilization"),
        value=value,
        source=NotBlankStr("workload"),
        timestamp=NOW,
    )


class _AlwaysHireStrategy:
    """Stub strategy that always proposes a hire."""

    @property
    def name(self) -> NotBlankStr:
        return NotBlankStr("stub_hire")

    @property
    def action_types(self) -> frozenset[ScalingActionType]:
        return frozenset({ScalingActionType.HIRE})

    async def evaluate(
        self,
        context: object,
    ) -> tuple[ScalingDecision, ...]:
        return (
            make_decision(
                action_type=ScalingActionType.HIRE,
                source_strategy=ScalingStrategyName.WORKLOAD,
                target_role="developer",
            ),
        )


class _EmptyStrategy:
    """Stub strategy that proposes nothing."""

    @property
    def name(self) -> NotBlankStr:
        return NotBlankStr("stub_empty")

    @property
    def action_types(self) -> frozenset[ScalingActionType]:
        return frozenset()

    async def evaluate(
        self,
        context: object,
    ) -> tuple[ScalingDecision, ...]:
        return ()


class _PassthroughGuard:
    """Stub guard that passes everything through."""

    @property
    def name(self) -> NotBlankStr:
        return NotBlankStr("passthrough")

    async def filter(
        self,
        decisions: tuple[ScalingDecision, ...],
    ) -> tuple[ScalingDecision, ...]:
        return decisions


_AGENT_IDS = (NotBlankStr("a1"), NotBlankStr("a2"))


@pytest.mark.unit
class TestScalingService:
    """ScalingService pipeline orchestration."""

    async def test_evaluate_runs_strategies(self) -> None:
        service = ScalingService(
            strategies=(_AlwaysHireStrategy(),),
            guard=_PassthroughGuard(),
            context_builder=ScalingContextBuilder(),
            config=ScalingConfig(),
        )
        decisions = await service.evaluate(agent_ids=_AGENT_IDS)
        assert len(decisions) == 1
        assert decisions[0].action_type == ScalingActionType.HIRE

    async def test_evaluate_multiple_strategies(self) -> None:
        service = ScalingService(
            strategies=(_AlwaysHireStrategy(), _EmptyStrategy()),
            guard=_PassthroughGuard(),
            context_builder=ScalingContextBuilder(),
            config=ScalingConfig(),
        )
        decisions = await service.evaluate(agent_ids=_AGENT_IDS)
        assert len(decisions) == 1

    async def test_evaluate_applies_guards(self) -> None:
        # Rate limit blocks all hires.
        rate_limit = RateLimitGuard(max_hires_per_day=0)
        guard = CompositeScalingGuard(guards=(rate_limit,))

        service = ScalingService(
            strategies=(_AlwaysHireStrategy(),),
            guard=guard,
            context_builder=ScalingContextBuilder(),
            config=ScalingConfig(),
        )
        decisions = await service.evaluate(agent_ids=_AGENT_IDS)
        assert len(decisions) == 0

    async def test_evaluate_disabled_returns_empty(self) -> None:
        config = ScalingConfig(enabled=False)
        service = ScalingService(
            strategies=(_AlwaysHireStrategy(),),
            guard=_PassthroughGuard(),
            context_builder=ScalingContextBuilder(),
            config=config,
        )
        decisions = await service.evaluate(agent_ids=_AGENT_IDS)
        assert len(decisions) == 0

    async def test_recent_decisions_tracked(self) -> None:
        service = ScalingService(
            strategies=(_AlwaysHireStrategy(),),
            guard=_PassthroughGuard(),
            context_builder=ScalingContextBuilder(),
            config=ScalingConfig(),
        )
        await service.evaluate(agent_ids=_AGENT_IDS)
        recent = service.get_recent_decisions()
        assert len(recent) == 1

    async def test_record_action(self) -> None:
        service = ScalingService(
            strategies=(),
            guard=_PassthroughGuard(),
            context_builder=ScalingContextBuilder(),
            config=ScalingConfig(),
        )
        record = ScalingActionRecord(
            decision_id=NotBlankStr("d1"),
            outcome=ScalingOutcome.EXECUTED,
            result_id=NotBlankStr("hire-request-1"),
            executed_at=NOW,
        )
        service.record_action(record)
        recent = service.get_recent_actions()
        assert len(recent) == 1
        assert recent[0].outcome == ScalingOutcome.EXECUTED

    async def test_empty_strategies_returns_empty(self) -> None:
        service = ScalingService(
            strategies=(),
            guard=_PassthroughGuard(),
            context_builder=ScalingContextBuilder(),
            config=ScalingConfig(),
        )
        decisions = await service.evaluate(agent_ids=_AGENT_IDS)
        assert len(decisions) == 0

    async def test_update_signal_primes_threshold_trigger(self) -> None:
        trigger = SignalThresholdTrigger(
            signal_name=NotBlankStr("avg_utilization"),
            threshold=0.85,
            above=True,
        )
        service = ScalingService(
            strategies=(_AlwaysHireStrategy(),),
            trigger=trigger,
            guard=_PassthroughGuard(),
            context_builder=ScalingContextBuilder(),
            config=ScalingConfig(),
        )
        # First signal initialises state; second crosses the threshold.
        await service.update_signal(_utilization_signal(0.50))
        await service.update_signal(_utilization_signal(0.95))
        decisions = await service.evaluate(agent_ids=_AGENT_IDS)
        assert len(decisions) == 1

    async def test_update_signal_without_crossing_stays_gated(self) -> None:
        trigger = SignalThresholdTrigger(
            signal_name=NotBlankStr("avg_utilization"),
            threshold=0.85,
            above=True,
        )
        service = ScalingService(
            strategies=(_AlwaysHireStrategy(),),
            trigger=trigger,
            guard=_PassthroughGuard(),
            context_builder=ScalingContextBuilder(),
            config=ScalingConfig(),
        )
        await service.update_signal(_utilization_signal(0.50))
        decisions = await service.evaluate(agent_ids=_AGENT_IDS)
        assert decisions == ()

    async def test_update_signal_noop_for_batched_trigger(self) -> None:
        service = ScalingService(
            strategies=(_AlwaysHireStrategy(),),
            trigger=BatchedScalingTrigger(interval_seconds=900),
            guard=_PassthroughGuard(),
            context_builder=ScalingContextBuilder(),
            config=ScalingConfig(),
        )
        # Batched trigger ignores pushed signals; the push must not raise
        # and the first evaluation still fires (interval elapsed).
        await service.update_signal(_utilization_signal(0.95))
        decisions = await service.evaluate(agent_ids=_AGENT_IDS)
        assert len(decisions) == 1
