"""Tests for scaling factory."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.hr.scaling.config import (
    BudgetCapConfig,
    PerformancePruningConfig,
    ScalingConfig,
    SkillGapConfig,
    TriggerConfig,
    WorkloadScalingConfig,
)
from synthorg.hr.scaling.factory import (
    create_scaling_context_builder,
    create_scaling_guards,
    create_scaling_strategies,
    create_scaling_trigger,
)
from synthorg.hr.scaling.triggers.batched import BatchedScalingTrigger
from synthorg.hr.scaling.triggers.composite import CompositeScalingTrigger
from synthorg.hr.scaling.triggers.threshold import SignalThresholdTrigger
from synthorg.meta.learning_curve import ScorecardSummary, append_summary


def _summary(label: str, total: int, *, hour: int) -> ScorecardSummary:
    return ScorecardSummary(
        run_label=label,
        generated_at=datetime(2026, 4, 11, hour, 0, 0, tzinfo=UTC),
        total=total,
        max_total=100,
        is_passing=total >= 50,
    )


@pytest.mark.unit
class TestCreateScalingStrategies:
    """Strategy creation from config."""

    @pytest.mark.parametrize(
        ("config", "expected_count"),
        [
            (ScalingConfig(), 2),
            (ScalingConfig(skill_gap=SkillGapConfig(enabled=True)), 3),
            (
                ScalingConfig(
                    workload=WorkloadScalingConfig(enabled=False),
                    budget_cap=BudgetCapConfig(enabled=False),
                    skill_gap=SkillGapConfig(enabled=False),
                    performance_pruning=PerformancePruningConfig(enabled=False),
                ),
                0,
            ),
        ],
        ids=["default", "all-enabled", "all-disabled"],
    )
    def test_strategy_creation(
        self,
        config: ScalingConfig,
        expected_count: int,
    ) -> None:
        strategies = create_scaling_strategies(config)
        assert len(strategies) == expected_count


@pytest.mark.unit
class TestCreateScalingGuards:
    """Guard chain creation from config."""

    @pytest.mark.parametrize(
        "approval_store",
        [
            None,
            "approval_store",
        ],
        ids=[
            "creates-composite-without-approval",
            "creates-composite-with-approval",
        ],
    )
    def test_guard_creation(self, approval_store: str | None) -> None:
        from synthorg.api.approval_store import ApprovalStore

        config = ScalingConfig()
        if approval_store is not None:
            store = ApprovalStore()
            guard = create_scaling_guards(config, approval_store=store)
        else:
            guard = create_scaling_guards(config)
        assert guard.name == "composite"


@pytest.mark.unit
class TestCreateScalingTrigger:
    """Trigger creation from config."""

    def test_creates_batched_trigger(self) -> None:
        config = ScalingConfig()
        trigger = create_scaling_trigger(config)
        assert trigger.name == "batched"
        assert isinstance(trigger, BatchedScalingTrigger)

    def test_creates_signal_threshold_trigger(self) -> None:
        config = ScalingConfig(triggers=TriggerConfig(type="signal_threshold"))
        trigger = create_scaling_trigger(config)
        assert trigger.name == "signal_threshold"
        assert isinstance(trigger, SignalThresholdTrigger)

    def test_creates_composite_trigger(self) -> None:
        config = ScalingConfig(triggers=TriggerConfig(type="composite"))
        trigger = create_scaling_trigger(config)
        assert trigger.name == "composite"
        assert isinstance(trigger, CompositeScalingTrigger)

    def test_composite_with_single_member(self) -> None:
        config = ScalingConfig(
            triggers=TriggerConfig(
                type="composite",
                composite_members=("signal_threshold",),
            )
        )
        trigger = create_scaling_trigger(config)
        assert isinstance(trigger, CompositeScalingTrigger)


@pytest.mark.unit
class TestCreateScalingContextBuilder:
    """Context builder creation from config."""

    def test_creates_builder(self) -> None:
        config = ScalingConfig()
        builder = create_scaling_context_builder(config)
        assert builder is not None

    async def test_builder_surfaces_benchmark_regression(self, tmp_path: Path) -> None:
        """A configured history dir wires the benchmark source end to end."""
        append_summary(tmp_path, _summary("run-1", total=90, hour=1))
        append_summary(tmp_path, _summary("run-2", total=40, hour=2))

        builder = create_scaling_context_builder(
            ScalingConfig(),
            benchmark_history_dir=tmp_path,
        )
        context = await builder.build(agent_ids=())

        regression = next(
            s for s in context.benchmark_signals if s.name == "benchmark_is_regression"
        )
        assert regression.value == 1.0

    async def test_builder_without_history_dir_has_no_benchmark_signals(self) -> None:
        """No history dir leaves the benchmark signal absent."""
        builder = create_scaling_context_builder(ScalingConfig())
        context = await builder.build(agent_ids=())
        assert context.benchmark_signals == ()
