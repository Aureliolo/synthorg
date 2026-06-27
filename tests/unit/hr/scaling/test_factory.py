"""Tests for scaling factory."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.hr.hiring_service import HiringService
from synthorg.hr.offboarding_service import OffboardingService
from synthorg.hr.pruning.policy import PruningPolicy
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.scaling.config import (
    BudgetCapConfig,
    PerformancePruningConfig,
    ScalingConfig,
    SkillGapConfig,
    TriggerConfig,
    WorkloadScalingConfig,
)
from synthorg.hr.scaling.context import ScalingContextBuilder
from synthorg.hr.scaling.enums import ScalingStrategyName
from synthorg.hr.scaling.factory import (
    build_scaling_service,
    create_scaling_context_builder,
    create_scaling_guards,
    create_scaling_strategies,
    create_scaling_trigger,
)
from synthorg.hr.scaling.service import ScalingService
from synthorg.hr.scaling.strategies.performance_pruning import (
    PerformancePruningStrategy,
)
from synthorg.hr.scaling.triggers.batched import BatchedScalingTrigger
from synthorg.hr.scaling.triggers.composite import CompositeScalingTrigger
from synthorg.hr.scaling.triggers.threshold import SignalThresholdTrigger
from synthorg.meta.learning_curve import ScorecardSummary, append_summary
from tests._shared import mock_of


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
        assert isinstance(builder, ScalingContextBuilder)

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


@pytest.mark.unit
class TestBuildScalingService:
    """Full-pipeline assembly via ``build_scaling_service``."""

    def _names(self, service: ScalingService) -> set[str]:
        return {str(s.name) for s in service.strategies}

    def test_assembles_service_with_enabled_strategies(self) -> None:
        service = build_scaling_service(ScalingConfig())
        assert isinstance(service, ScalingService)
        names = self._names(service)
        assert ScalingStrategyName.WORKLOAD.value in names
        assert ScalingStrategyName.BUDGET_CAP.value in names
        assert ScalingStrategyName.SKILL_GAP.value not in names

    def test_injects_default_pruning_policy(self) -> None:
        # Unlike the bare create_scaling_strategies (which skips performance
        # pruning without a policy), the service factory injects a default
        # ThresholdPruningPolicy so the strategy is live.
        service = build_scaling_service(ScalingConfig())
        assert ScalingStrategyName.PERFORMANCE_PRUNING.value in self._names(service)

    def test_all_strategies_disabled_yields_empty(self) -> None:
        config = ScalingConfig(
            workload=WorkloadScalingConfig(enabled=False),
            budget_cap=BudgetCapConfig(enabled=False),
            skill_gap=SkillGapConfig(enabled=False),
            performance_pruning=PerformancePruningConfig(enabled=False),
        )
        service = build_scaling_service(config)
        assert service.strategies == ()

    def test_threads_execution_collaborators(self) -> None:
        hiring = mock_of[HiringService]()
        offboarding = mock_of[OffboardingService]()
        registry = AgentRegistryService()
        service = build_scaling_service(
            ScalingConfig(),
            hiring_service=hiring,
            offboarding_service=offboarding,
            agent_registry=registry,
        )
        # The named regression is build_scaling_service dropping a collaborator
        # on the floor, so assert each is threaded onto the orchestrator verbatim
        # rather than only re-checking default config/strategy wiring.
        assert service._hiring_service is hiring
        assert service._offboarding_service is offboarding
        assert service._agent_registry is registry
        assert service.config.enabled is True
        assert ScalingStrategyName.WORKLOAD.value in self._names(service)
        assert ScalingStrategyName.BUDGET_CAP.value in self._names(service)

    def test_explicit_pruning_policy_is_not_overridden(self) -> None:
        # A caller-supplied policy must propagate to the performance-pruning
        # strategy verbatim, not be replaced by the default ThresholdPruningPolicy.
        explicit = mock_of[PruningPolicy]()
        service = build_scaling_service(ScalingConfig(), pruning_policy=explicit)
        pruning = next(
            s for s in service.strategies if isinstance(s, PerformancePruningStrategy)
        )
        assert pruning._policy is explicit
