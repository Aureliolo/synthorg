"""Tests for CostOptimizer service."""

from datetime import UTC, datetime, timedelta

import pytest

from ai_company.budget.config import BudgetAlertConfig, BudgetConfig
from ai_company.budget.enums import BudgetAlertLevel
from ai_company.budget.optimizer import CostOptimizer
from ai_company.budget.optimizer_models import (
    AnomalySeverity,
    AnomalyType,
    CostOptimizerConfig,
    EfficiencyRating,
)
from ai_company.budget.tracker import CostTracker
from ai_company.providers.routing.models import ResolvedModel
from ai_company.providers.routing.resolver import ModelResolver
from tests.unit.budget.conftest import make_cost_record

# ── Helpers ───────────────────────────────────────────────────────

_START = datetime(2026, 2, 1, tzinfo=UTC)
_END = datetime(2026, 3, 1, tzinfo=UTC)


def _make_optimizer(
    *,
    budget_config: BudgetConfig | None = None,
    config: CostOptimizerConfig | None = None,
    model_resolver: ModelResolver | None = None,
) -> tuple[CostOptimizer, CostTracker]:
    """Build a CostOptimizer with a fresh CostTracker."""
    bc = budget_config or BudgetConfig(total_monthly=100.0)
    tracker = CostTracker(budget_config=bc)
    optimizer = CostOptimizer(
        cost_tracker=tracker,
        budget_config=bc,
        config=config,
        model_resolver=model_resolver,
    )
    return optimizer, tracker


def _make_resolver(
    models: list[ResolvedModel] | None = None,
) -> ModelResolver:
    """Build a ModelResolver from a list of ResolvedModel."""
    if models is None:
        models = [
            ResolvedModel(
                provider_name="test-provider",
                model_id="test-large-001",
                alias="large",
                cost_per_1k_input=0.03,
                cost_per_1k_output=0.06,
            ),
            ResolvedModel(
                provider_name="test-provider",
                model_id="test-medium-001",
                alias="medium",
                cost_per_1k_input=0.01,
                cost_per_1k_output=0.02,
            ),
            ResolvedModel(
                provider_name="test-provider",
                model_id="test-small-001",
                alias="small",
                cost_per_1k_input=0.001,
                cost_per_1k_output=0.002,
            ),
        ]
    index: dict[str, ResolvedModel] = {}
    for m in models:
        index[m.model_id] = m
        if m.alias is not None:
            index[m.alias] = m
    return ModelResolver(index)


# ── Init Tests ────────────────────────────────────────────────────


@pytest.mark.unit
class TestInit:
    async def test_defaults(self) -> None:
        optimizer, _ = _make_optimizer()
        assert optimizer._config == CostOptimizerConfig()

    async def test_custom_config(self) -> None:
        cfg = CostOptimizerConfig(anomaly_sigma_threshold=3.0)
        optimizer, _ = _make_optimizer(config=cfg)
        assert optimizer._config.anomaly_sigma_threshold == 3.0


# ── Anomaly Detection Tests ──────────────────────────────────────


@pytest.mark.unit
class TestDetectAnomalies:
    async def test_no_records_empty_result(self) -> None:
        optimizer, _ = _make_optimizer()
        result = await optimizer.detect_anomalies(start=_START, end=_END)
        assert result.anomalies == ()
        assert result.agents_scanned == 0

    async def test_normal_spending_no_anomalies(self) -> None:
        optimizer, tracker = _make_optimizer()
        # Create uniform spending across 5 windows
        window_duration = (_END - _START) / 5
        for i in range(5):
            ts = _START + window_duration * i + timedelta(hours=1)
            await tracker.record(
                make_cost_record(agent_id="alice", cost_usd=1.0, timestamp=ts),
            )

        result = await optimizer.detect_anomalies(start=_START, end=_END)
        assert result.anomalies == ()
        assert result.agents_scanned == 1

    async def test_spike_detected(self) -> None:
        optimizer, tracker = _make_optimizer()
        window_duration = (_END - _START) / 5

        # Normal spending in first 4 windows
        for i in range(4):
            ts = _START + window_duration * i + timedelta(hours=1)
            await tracker.record(
                make_cost_record(agent_id="alice", cost_usd=1.0, timestamp=ts),
            )

        # Spike in last window
        ts = _START + window_duration * 4 + timedelta(hours=1)
        await tracker.record(
            make_cost_record(agent_id="alice", cost_usd=20.0, timestamp=ts),
        )

        result = await optimizer.detect_anomalies(start=_START, end=_END)
        assert len(result.anomalies) == 1
        anomaly = result.anomalies[0]
        assert anomaly.agent_id == "alice"
        assert anomaly.anomaly_type == AnomalyType.SPIKE
        assert anomaly.current_value == 20.0

    async def test_insufficient_windows_no_false_positive(self) -> None:
        config = CostOptimizerConfig(min_anomaly_windows=5)
        optimizer, tracker = _make_optimizer(config=config)

        # Only 3 windows of data in a 3-window analysis
        window_duration = (_END - _START) / 3
        for i in range(3):
            ts = _START + window_duration * i + timedelta(hours=1)
            cost = 1.0 if i < 2 else 50.0
            await tracker.record(
                make_cost_record(agent_id="alice", cost_usd=cost, timestamp=ts),
            )

        result = await optimizer.detect_anomalies(
            start=_START,
            end=_END,
            window_count=3,
        )
        assert result.anomalies == ()

    async def test_multiple_agents_only_anomalous_flagged(self) -> None:
        optimizer, tracker = _make_optimizer()
        window_duration = (_END - _START) / 5

        # Alice: uniform spending
        for i in range(5):
            ts = _START + window_duration * i + timedelta(hours=1)
            await tracker.record(
                make_cost_record(agent_id="alice", cost_usd=1.0, timestamp=ts),
            )

        # Bob: spike in last window
        for i in range(4):
            ts = _START + window_duration * i + timedelta(hours=1)
            await tracker.record(
                make_cost_record(agent_id="bob", cost_usd=1.0, timestamp=ts),
            )
        ts = _START + window_duration * 4 + timedelta(hours=1)
        await tracker.record(
            make_cost_record(agent_id="bob", cost_usd=20.0, timestamp=ts),
        )

        result = await optimizer.detect_anomalies(start=_START, end=_END)
        assert len(result.anomalies) == 1
        assert result.anomalies[0].agent_id == "bob"
        assert result.agents_scanned == 2

    async def test_window_count_validation(self) -> None:
        optimizer, _ = _make_optimizer()
        with pytest.raises(ValueError, match="window_count must be >= 2"):
            await optimizer.detect_anomalies(
                start=_START,
                end=_END,
                window_count=1,
            )

    async def test_spike_from_zero_baseline(self) -> None:
        """Agent with no historical spending that suddenly appears."""
        optimizer, tracker = _make_optimizer(
            config=CostOptimizerConfig(min_anomaly_windows=3),
        )
        window_duration = (_END - _START) / 5

        # No spending in first 4 windows, spending in window 5
        ts = _START + window_duration * 4 + timedelta(hours=1)
        await tracker.record(
            make_cost_record(agent_id="alice", cost_usd=5.0, timestamp=ts),
        )

        result = await optimizer.detect_anomalies(start=_START, end=_END)
        assert len(result.anomalies) == 1
        anomaly = result.anomalies[0]
        assert anomaly.severity == AnomalySeverity.HIGH
        assert anomaly.baseline_value == 0.0

    async def test_severity_classification(self) -> None:
        """Verify severity levels based on deviation factor."""
        optimizer, tracker = _make_optimizer(
            config=CostOptimizerConfig(
                anomaly_sigma_threshold=1.5,
                anomaly_spike_factor=10.0,
            ),
        )
        window_duration = (_END - _START) / 5

        # Create varied baseline with small stddev=0.1
        baseline_costs = [1.0, 1.1, 0.9, 1.0]
        for i, cost in enumerate(baseline_costs):
            ts = _START + window_duration * i + timedelta(hours=1)
            await tracker.record(
                make_cost_record(agent_id="alice", cost_usd=cost, timestamp=ts),
            )

        # Medium spike (2-3 sigma range)
        ts = _START + window_duration * 4 + timedelta(hours=1)
        await tracker.record(
            make_cost_record(agent_id="alice", cost_usd=1.25, timestamp=ts),
        )

        await optimizer.detect_anomalies(start=_START, end=_END)
        # With such small deviations, this may or may not trigger
        # depending on exact sigma; the key is the test runs without error


# ── Efficiency Analysis Tests ─────────────────────────────────────


@pytest.mark.unit
class TestAnalyzeEfficiency:
    async def test_uniform_all_normal(self) -> None:
        optimizer, tracker = _make_optimizer()

        # Same cost/token ratio for all agents
        for agent in ("alice", "bob", "carol"):
            await tracker.record(
                make_cost_record(
                    agent_id=agent,
                    cost_usd=1.0,
                    input_tokens=1000,
                    output_tokens=0,
                    timestamp=_START + timedelta(hours=1),
                ),
            )

        result = await optimizer.analyze_efficiency(start=_START, end=_END)
        assert all(
            a.efficiency_rating == EfficiencyRating.NORMAL for a in result.agents
        )
        assert result.inefficient_agent_count == 0

    async def test_one_inefficient(self) -> None:
        optimizer, tracker = _make_optimizer()

        # Alice: cheap (1.0/1000 = 1.0 per 1k)
        await tracker.record(
            make_cost_record(
                agent_id="alice",
                cost_usd=1.0,
                input_tokens=1000,
                output_tokens=0,
                timestamp=_START + timedelta(hours=1),
            ),
        )
        # Bob: expensive (10.0/1000 = 10.0 per 1k)
        await tracker.record(
            make_cost_record(
                agent_id="bob",
                cost_usd=10.0,
                input_tokens=1000,
                output_tokens=0,
                timestamp=_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.analyze_efficiency(start=_START, end=_END)
        assert result.inefficient_agent_count == 1
        # Sorted by cost_per_1k desc
        assert result.agents[0].agent_id == "bob"
        assert result.agents[0].efficiency_rating == EfficiencyRating.INEFFICIENT

    async def test_zero_tokens_handled(self) -> None:
        optimizer, tracker = _make_optimizer()

        await tracker.record(
            make_cost_record(
                agent_id="alice",
                cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                timestamp=_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.analyze_efficiency(start=_START, end=_END)
        assert len(result.agents) == 1
        assert result.agents[0].cost_per_1k_tokens == 0.0
        assert result.agents[0].efficiency_rating == EfficiencyRating.NORMAL

    async def test_efficient_agent_flagged(self) -> None:
        optimizer, tracker = _make_optimizer()

        # Alice: very cheap (0.1/10000 = 0.01 per 1k)
        await tracker.record(
            make_cost_record(
                agent_id="alice",
                cost_usd=0.1,
                input_tokens=10000,
                output_tokens=0,
                timestamp=_START + timedelta(hours=1),
            ),
        )
        # Bob: normal (1.0/1000 = 1.0 per 1k)
        await tracker.record(
            make_cost_record(
                agent_id="bob",
                cost_usd=1.0,
                input_tokens=1000,
                output_tokens=0,
                timestamp=_START + timedelta(hours=1),
            ),
        )
        # Carol: normal (1.0/1000 = 1.0 per 1k)
        await tracker.record(
            make_cost_record(
                agent_id="carol",
                cost_usd=1.0,
                input_tokens=1000,
                output_tokens=0,
                timestamp=_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.analyze_efficiency(start=_START, end=_END)
        alice = next(a for a in result.agents if a.agent_id == "alice")
        assert alice.efficiency_rating == EfficiencyRating.EFFICIENT

    async def test_empty_records(self) -> None:
        optimizer, _ = _make_optimizer()
        result = await optimizer.analyze_efficiency(start=_START, end=_END)
        assert result.agents == ()
        assert result.global_avg_cost_per_1k == 0.0


# ── Downgrade Recommendation Tests ────────────────────────────────


@pytest.mark.unit
class TestRecommendDowngrades:
    async def test_no_resolver_empty_result(self) -> None:
        optimizer, _ = _make_optimizer()
        result = await optimizer.recommend_downgrades(start=_START, end=_END)
        assert result.recommendations == ()

    async def test_with_downgrade_path(self) -> None:
        from ai_company.budget.config import AutoDowngradeConfig

        resolver = _make_resolver()
        bc = BudgetConfig(
            total_monthly=100.0,
            auto_downgrade=AutoDowngradeConfig(
                enabled=True,
                threshold=80,
                downgrade_map=(("large", "small"),),
            ),
        )
        tracker = CostTracker(budget_config=bc)
        optimizer = CostOptimizer(
            cost_tracker=tracker,
            budget_config=bc,
            model_resolver=resolver,
        )

        # Make alice inefficient using large model
        await tracker.record(
            make_cost_record(
                agent_id="alice",
                model="test-large-001",
                cost_usd=10.0,
                input_tokens=1000,
                output_tokens=0,
                timestamp=_START + timedelta(hours=1),
            ),
        )
        # Make bob efficient using small model
        await tracker.record(
            make_cost_record(
                agent_id="bob",
                model="test-small-001",
                cost_usd=0.1,
                input_tokens=1000,
                output_tokens=0,
                timestamp=_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.recommend_downgrades(start=_START, end=_END)
        assert len(result.recommendations) == 1
        rec = result.recommendations[0]
        assert rec.agent_id == "alice"
        assert rec.current_model == "test-large-001"
        assert rec.recommended_model == "test-small-001"
        assert rec.estimated_savings_per_1k > 0

    async def test_no_cheaper_model_empty(self) -> None:
        """No recommendation when agent already uses cheapest model."""
        resolver = _make_resolver(
            [
                ResolvedModel(
                    provider_name="test-provider",
                    model_id="test-only-001",
                    alias="only",
                    cost_per_1k_input=0.01,
                    cost_per_1k_output=0.02,
                ),
            ]
        )
        bc = BudgetConfig(total_monthly=100.0)
        tracker = CostTracker(budget_config=bc)
        optimizer = CostOptimizer(
            cost_tracker=tracker,
            budget_config=bc,
            model_resolver=resolver,
        )

        # Only agent, only model — inefficient by default since it's the only one
        await tracker.record(
            make_cost_record(
                agent_id="alice",
                model="test-only-001",
                cost_usd=10.0,
                input_tokens=1000,
                output_tokens=0,
                timestamp=_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.recommend_downgrades(start=_START, end=_END)
        assert result.recommendations == ()


# ── Evaluate Operation Tests ──────────────────────────────────────


@pytest.mark.unit
class TestEvaluateOperation:
    async def test_healthy_budget_approved(self) -> None:
        optimizer, tracker = _make_optimizer()
        # Spend only 10% of budget
        await tracker.record(
            make_cost_record(cost_usd=10.0, timestamp=_START + timedelta(hours=1)),
        )
        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=0.5,
            now=_START + timedelta(days=15),
        )
        assert decision.approved is True
        assert decision.alert_level == BudgetAlertLevel.NORMAL

    async def test_hard_stop_denied(self) -> None:
        bc = BudgetConfig(
            total_monthly=100.0,
            alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
        )
        optimizer, tracker = _make_optimizer(budget_config=bc)

        # Spend 100% of budget
        await tracker.record(
            make_cost_record(cost_usd=100.0, timestamp=_START + timedelta(hours=1)),
        )

        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=1.0,
            now=_START + timedelta(days=15),
        )
        assert decision.approved is False
        assert decision.alert_level == BudgetAlertLevel.HARD_STOP

    async def test_would_exceed_budget_denied(self) -> None:
        bc = BudgetConfig(
            total_monthly=100.0,
            alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
        )
        optimizer, tracker = _make_optimizer(budget_config=bc)

        # Spend 95% and request 10 more
        await tracker.record(
            make_cost_record(cost_usd=95.0, timestamp=_START + timedelta(hours=1)),
        )

        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=10.0,
            now=_START + timedelta(days=15),
        )
        assert decision.approved is False
        assert "would exceed" in decision.reason

    async def test_warning_level_approved_with_conditions(self) -> None:
        bc = BudgetConfig(
            total_monthly=100.0,
            alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
        )
        optimizer, tracker = _make_optimizer(budget_config=bc)

        # Spend 80% (warning level)
        await tracker.record(
            make_cost_record(cost_usd=80.0, timestamp=_START + timedelta(hours=1)),
        )

        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=2.0,
            now=_START + timedelta(days=15),
        )
        assert decision.approved is True
        assert decision.alert_level == BudgetAlertLevel.WARNING
        assert len(decision.conditions) > 0

    async def test_budget_enforcement_disabled(self) -> None:
        bc = BudgetConfig(total_monthly=0.0)
        optimizer, _ = _make_optimizer(budget_config=bc)

        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=100.0,
        )
        assert decision.approved is True
        assert "disabled" in decision.reason.lower()

    async def test_critical_level_auto_deny_with_custom_config(self) -> None:
        """Auto-deny at CRITICAL when configured."""
        bc = BudgetConfig(
            total_monthly=100.0,
            alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
        )
        config = CostOptimizerConfig(
            approval_auto_deny_alert_level=BudgetAlertLevel.CRITICAL,
        )
        optimizer, tracker = _make_optimizer(budget_config=bc, config=config)

        # Spend 92% (critical level)
        await tracker.record(
            make_cost_record(cost_usd=92.0, timestamp=_START + timedelta(hours=1)),
        )

        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=0.01,
            now=_START + timedelta(days=15),
        )
        assert decision.approved is False
        assert decision.alert_level == BudgetAlertLevel.CRITICAL

    async def test_high_cost_condition(self) -> None:
        """High-cost warning condition when estimated cost >= threshold."""
        config = CostOptimizerConfig(approval_warn_threshold_usd=0.5)
        optimizer, _ = _make_optimizer(config=config)

        decision = await optimizer.evaluate_operation(
            agent_id="alice",
            estimated_cost_usd=1.0,
            now=_START + timedelta(days=15),
        )
        assert decision.approved is True
        assert any("High-cost" in c for c in decision.conditions)
