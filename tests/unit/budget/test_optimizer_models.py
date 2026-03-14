"""Tests for CFO optimizer domain models."""

from datetime import UTC, datetime

import pytest

from synthorg.budget.enums import BudgetAlertLevel
from synthorg.budget.optimizer_models import (
    AgentEfficiency,
    AnomalyDetectionResult,
    AnomalySeverity,
    AnomalyType,
    ApprovalDecision,
    CostOptimizerConfig,
    DowngradeAnalysis,
    DowngradeRecommendation,
    EfficiencyAnalysis,
    EfficiencyRating,
    RoutingOptimizationAnalysis,
    RoutingSuggestion,
    SpendingAnomaly,
)

# ── Enum Tests ────────────────────────────────────────────────────


class TestAnomalyType:
    @pytest.mark.unit
    def test_values(self) -> None:
        assert AnomalyType.SPIKE.value == "spike"
        assert AnomalyType.SUSTAINED_HIGH.value == "sustained_high"
        assert AnomalyType.RATE_INCREASE.value == "rate_increase"

    @pytest.mark.unit
    def test_member_count(self) -> None:
        assert len(AnomalyType) == 3


class TestAnomalySeverity:
    @pytest.mark.unit
    def test_values(self) -> None:
        assert AnomalySeverity.LOW.value == "low"
        assert AnomalySeverity.MEDIUM.value == "medium"
        assert AnomalySeverity.HIGH.value == "high"


class TestEfficiencyRating:
    @pytest.mark.unit
    def test_values(self) -> None:
        assert EfficiencyRating.EFFICIENT.value == "efficient"
        assert EfficiencyRating.NORMAL.value == "normal"
        assert EfficiencyRating.INEFFICIENT.value == "inefficient"


# ── SpendingAnomaly Tests ─────────────────────────────────────────


class TestSpendingAnomaly:
    @pytest.mark.unit
    def test_construction(self) -> None:
        anomaly = SpendingAnomaly(
            agent_id="alice",
            anomaly_type=AnomalyType.SPIKE,
            severity=AnomalySeverity.HIGH,
            description="Test spike",
            current_value=10.0,
            baseline_value=2.0,
            deviation_factor=4.0,
            detected_at=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            period_start=datetime(2026, 2, 28, tzinfo=UTC),
            period_end=datetime(2026, 3, 1, tzinfo=UTC),
        )
        assert anomaly.agent_id == "alice"
        assert anomaly.anomaly_type == AnomalyType.SPIKE
        assert anomaly.severity == AnomalySeverity.HIGH
        assert anomaly.current_value == 10.0
        assert anomaly.baseline_value == 2.0

    @pytest.mark.unit
    def test_frozen(self) -> None:
        anomaly = SpendingAnomaly(
            agent_id="alice",
            anomaly_type=AnomalyType.SPIKE,
            severity=AnomalySeverity.LOW,
            description="Test",
            current_value=1.0,
            baseline_value=0.5,
            deviation_factor=1.5,
            detected_at=datetime(2026, 3, 1, tzinfo=UTC),
            period_start=datetime(2026, 2, 28, tzinfo=UTC),
            period_end=datetime(2026, 3, 1, tzinfo=UTC),
        )
        with pytest.raises(Exception):  # noqa: B017, PT011
            anomaly.agent_id = "bob"  # type: ignore[misc]

    @pytest.mark.unit
    def test_period_ordering_invalid(self) -> None:
        with pytest.raises(ValueError, match="period_start"):
            SpendingAnomaly(
                agent_id="alice",
                anomaly_type=AnomalyType.SPIKE,
                severity=AnomalySeverity.LOW,
                description="Test",
                current_value=1.0,
                baseline_value=0.5,
                deviation_factor=1.5,
                detected_at=datetime(2026, 3, 1, tzinfo=UTC),
                period_start=datetime(2026, 3, 2, tzinfo=UTC),
                period_end=datetime(2026, 3, 1, tzinfo=UTC),
            )


# ── AnomalyDetectionResult Tests ─────────────────────────────────


class TestAnomalyDetectionResult:
    @pytest.mark.unit
    def test_empty_result(self) -> None:
        result = AnomalyDetectionResult(
            anomalies=(),
            scan_period_start=datetime(2026, 2, 1, tzinfo=UTC),
            scan_period_end=datetime(2026, 3, 1, tzinfo=UTC),
            agents_scanned=0,
            scan_timestamp=datetime(2026, 3, 1, tzinfo=UTC),
        )
        assert result.anomalies == ()
        assert result.agents_scanned == 0

    @pytest.mark.unit
    def test_period_ordering_invalid(self) -> None:
        with pytest.raises(ValueError, match="scan_period_start"):
            AnomalyDetectionResult(
                scan_period_start=datetime(2026, 3, 1, tzinfo=UTC),
                scan_period_end=datetime(2026, 2, 1, tzinfo=UTC),
                agents_scanned=0,
                scan_timestamp=datetime(2026, 3, 1, tzinfo=UTC),
            )


# ── AgentEfficiency Tests ─────────────────────────────────────────


class TestAgentEfficiency:
    @pytest.mark.unit
    def test_construction(self) -> None:
        eff = AgentEfficiency(
            agent_id="alice",
            total_cost_usd=5.0,
            total_tokens=100000,
            record_count=50,
            efficiency_rating=EfficiencyRating.NORMAL,
        )
        assert eff.agent_id == "alice"
        assert eff.total_cost_usd == 5.0
        assert eff.efficiency_rating == EfficiencyRating.NORMAL
        assert eff.cost_per_1k_tokens == 0.05

    @pytest.mark.unit
    def test_zero_tokens(self) -> None:
        eff = AgentEfficiency(
            agent_id="alice",
            total_cost_usd=0.0,
            total_tokens=0,
            record_count=0,
            efficiency_rating=EfficiencyRating.NORMAL,
        )
        assert eff.total_tokens == 0
        assert eff.cost_per_1k_tokens == 0.0

    @pytest.mark.unit
    def test_cost_per_1k_is_computed(self) -> None:
        eff = AgentEfficiency(
            agent_id="alice",
            total_cost_usd=10.0,
            total_tokens=5000,
            record_count=10,
            efficiency_rating=EfficiencyRating.NORMAL,
        )
        assert eff.cost_per_1k_tokens == 2.0


# ── EfficiencyAnalysis Tests ─────────────────────────────────────


class TestEfficiencyAnalysis:
    @pytest.mark.unit
    def test_empty_analysis(self) -> None:
        analysis = EfficiencyAnalysis(
            agents=(),
            global_avg_cost_per_1k=0.0,
            analysis_period_start=datetime(2026, 2, 1, tzinfo=UTC),
            analysis_period_end=datetime(2026, 3, 1, tzinfo=UTC),
        )
        assert analysis.agents == ()
        assert analysis.inefficient_agent_count == 0

    @pytest.mark.unit
    def test_inefficient_count_is_computed(self) -> None:
        analysis = EfficiencyAnalysis(
            agents=(
                AgentEfficiency(
                    agent_id="alice",
                    total_cost_usd=10.0,
                    total_tokens=1000,
                    record_count=5,
                    efficiency_rating=EfficiencyRating.INEFFICIENT,
                ),
                AgentEfficiency(
                    agent_id="bob",
                    total_cost_usd=1.0,
                    total_tokens=1000,
                    record_count=5,
                    efficiency_rating=EfficiencyRating.NORMAL,
                ),
            ),
            global_avg_cost_per_1k=5.0,
            analysis_period_start=datetime(2026, 2, 1, tzinfo=UTC),
            analysis_period_end=datetime(2026, 3, 1, tzinfo=UTC),
        )
        assert analysis.inefficient_agent_count == 1

    @pytest.mark.unit
    def test_period_ordering_invalid(self) -> None:
        with pytest.raises(ValueError, match="analysis_period_start"):
            EfficiencyAnalysis(
                agents=(),
                global_avg_cost_per_1k=0.0,
                analysis_period_start=datetime(2026, 3, 1, tzinfo=UTC),
                analysis_period_end=datetime(2026, 2, 1, tzinfo=UTC),
            )


# ── DowngradeRecommendation Tests ─────────────────────────────────


class TestDowngradeRecommendation:
    @pytest.mark.unit
    def test_construction(self) -> None:
        rec = DowngradeRecommendation(
            agent_id="alice",
            current_model="test-large-001",
            recommended_model="test-small-001",
            estimated_savings_per_1k=0.05,
            reason="Switch to cheaper model",
        )
        assert rec.agent_id == "alice"
        assert rec.estimated_savings_per_1k == 0.05

    @pytest.mark.unit
    def test_frozen(self) -> None:
        rec = DowngradeRecommendation(
            agent_id="alice",
            current_model="test-large-001",
            recommended_model="test-small-001",
            estimated_savings_per_1k=0.05,
            reason="Switch to cheaper model",
        )
        with pytest.raises(Exception):  # noqa: B017, PT011
            rec.agent_id = "bob"  # type: ignore[misc]


# ── DowngradeAnalysis Tests ───────────────────────────────────────


class TestDowngradeAnalysis:
    @pytest.mark.unit
    def test_empty_analysis(self) -> None:
        analysis = DowngradeAnalysis(
            recommendations=(),
            budget_pressure_percent=0.0,
        )
        assert analysis.recommendations == ()
        assert analysis.total_estimated_savings_per_1k == 0.0

    @pytest.mark.unit
    def test_total_savings_is_computed(self) -> None:
        analysis = DowngradeAnalysis(
            recommendations=(
                DowngradeRecommendation(
                    agent_id="alice",
                    current_model="test-large-001",
                    recommended_model="test-small-001",
                    estimated_savings_per_1k=0.05,
                    reason="Switch to cheaper model",
                ),
                DowngradeRecommendation(
                    agent_id="bob",
                    current_model="test-large-001",
                    recommended_model="test-small-001",
                    estimated_savings_per_1k=0.03,
                    reason="Switch to cheaper model",
                ),
            ),
            budget_pressure_percent=50.0,
        )
        assert analysis.total_estimated_savings_per_1k == 0.08


# ── ApprovalDecision Tests ────────────────────────────────────────


class TestApprovalDecision:
    @pytest.mark.unit
    def test_approved(self) -> None:
        decision = ApprovalDecision(
            approved=True,
            reason="Approved",
            budget_remaining_usd=50.0,
            budget_used_percent=50.0,
            alert_level=BudgetAlertLevel.NORMAL,
            conditions=(),
        )
        assert decision.approved is True
        assert decision.budget_remaining_usd == 50.0

    @pytest.mark.unit
    def test_denied(self) -> None:
        decision = ApprovalDecision(
            approved=False,
            reason="Budget exhausted",
            budget_remaining_usd=0.0,
            budget_used_percent=100.0,
            alert_level=BudgetAlertLevel.HARD_STOP,
        )
        assert decision.approved is False
        assert decision.alert_level == BudgetAlertLevel.HARD_STOP

    @pytest.mark.unit
    def test_with_conditions(self) -> None:
        decision = ApprovalDecision(
            approved=True,
            reason="Approved with conditions",
            budget_remaining_usd=20.0,
            budget_used_percent=80.0,
            alert_level=BudgetAlertLevel.WARNING,
            conditions=("High cost operation", "Budget is running low"),
        )
        assert len(decision.conditions) == 2


# ── CostOptimizerConfig Tests ────────────────────────────────────


class TestCostOptimizerConfig:
    @pytest.mark.unit
    def test_defaults(self) -> None:
        config = CostOptimizerConfig()
        assert config.anomaly_sigma_threshold == 2.0
        assert config.anomaly_spike_factor == 3.0
        assert config.inefficiency_threshold_factor == 1.5
        assert config.approval_auto_deny_alert_level == BudgetAlertLevel.HARD_STOP
        assert config.approval_warn_threshold_usd == 1.0
        assert config.min_anomaly_windows == 3

    @pytest.mark.unit
    def test_custom_values(self) -> None:
        config = CostOptimizerConfig(
            anomaly_sigma_threshold=3.0,
            anomaly_spike_factor=5.0,
            inefficiency_threshold_factor=2.0,
            approval_auto_deny_alert_level=BudgetAlertLevel.CRITICAL,
            approval_warn_threshold_usd=2.5,
            min_anomaly_windows=4,
        )
        assert config.anomaly_sigma_threshold == 3.0
        assert config.anomaly_spike_factor == 5.0

    @pytest.mark.unit
    def test_sigma_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            CostOptimizerConfig(anomaly_sigma_threshold=0.0)

    @pytest.mark.unit
    def test_spike_factor_must_exceed_one(self) -> None:
        with pytest.raises(ValueError, match="greater than 1"):
            CostOptimizerConfig(anomaly_spike_factor=1.0)

    @pytest.mark.unit
    def test_inefficiency_factor_must_exceed_one(self) -> None:
        with pytest.raises(ValueError, match="greater than 1"):
            CostOptimizerConfig(inefficiency_threshold_factor=0.5)

    @pytest.mark.unit
    def test_min_anomaly_windows_minimum(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 2"):
            CostOptimizerConfig(min_anomaly_windows=1)

    @pytest.mark.unit
    def test_frozen(self) -> None:
        config = CostOptimizerConfig()
        with pytest.raises(Exception):  # noqa: B017, PT011
            config.anomaly_sigma_threshold = 5.0  # type: ignore[misc]


# ── DowngradeRecommendation validator tests ─────────────────────


class TestDowngradeRecommendationValidator:
    @pytest.mark.unit
    def test_same_model_rejected(self) -> None:
        with pytest.raises(ValueError, match="must differ"):
            DowngradeRecommendation(
                agent_id="alice",
                current_model="test-large-001",
                recommended_model="test-large-001",
                estimated_savings_per_1k=0.05,
                reason="No actual downgrade",
            )

    @pytest.mark.unit
    def test_zero_savings_rejected(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            DowngradeRecommendation(
                agent_id="alice",
                current_model="test-large-001",
                recommended_model="test-small-001",
                estimated_savings_per_1k=0.0,
                reason="Zero savings",
            )


# ── EfficiencyAnalysis sort-order validator tests ────────────────


class TestEfficiencyAnalysisSortOrder:
    @pytest.mark.unit
    def test_sorted_agents_accepted(self) -> None:
        analysis = EfficiencyAnalysis(
            agents=(
                AgentEfficiency(
                    agent_id="bob",
                    total_cost_usd=10.0,
                    total_tokens=1000,
                    record_count=5,
                    efficiency_rating=EfficiencyRating.INEFFICIENT,
                ),
                AgentEfficiency(
                    agent_id="alice",
                    total_cost_usd=1.0,
                    total_tokens=1000,
                    record_count=5,
                    efficiency_rating=EfficiencyRating.NORMAL,
                ),
            ),
            global_avg_cost_per_1k=5.0,
            analysis_period_start=datetime(2026, 2, 1, tzinfo=UTC),
            analysis_period_end=datetime(2026, 3, 1, tzinfo=UTC),
        )
        assert len(analysis.agents) == 2

    @pytest.mark.unit
    def test_unsorted_agents_rejected(self) -> None:
        with pytest.raises(ValueError, match="agents must be sorted"):
            EfficiencyAnalysis(
                agents=(
                    AgentEfficiency(
                        agent_id="alice",
                        total_cost_usd=1.0,
                        total_tokens=1000,
                        record_count=5,
                        efficiency_rating=EfficiencyRating.NORMAL,
                    ),
                    AgentEfficiency(
                        agent_id="bob",
                        total_cost_usd=10.0,
                        total_tokens=1000,
                        record_count=5,
                        efficiency_rating=EfficiencyRating.INEFFICIENT,
                    ),
                ),
                global_avg_cost_per_1k=5.0,
                analysis_period_start=datetime(2026, 2, 1, tzinfo=UTC),
                analysis_period_end=datetime(2026, 3, 1, tzinfo=UTC),
            )


# ── RoutingSuggestion Tests ──────────────────────────────────────


class TestRoutingSuggestion:
    @pytest.mark.unit
    def test_construction(self) -> None:
        suggestion = RoutingSuggestion(
            agent_id="alice",
            current_model="test-large-001",
            suggested_model="test-small-001",
            current_cost_per_1k=0.09,
            suggested_cost_per_1k=0.003,
            reason="Switch to cheaper model",
        )
        assert suggestion.agent_id == "alice"
        assert suggestion.estimated_savings_per_1k == 0.087

    @pytest.mark.unit
    def test_frozen(self) -> None:
        suggestion = RoutingSuggestion(
            agent_id="alice",
            current_model="test-large-001",
            suggested_model="test-small-001",
            current_cost_per_1k=0.09,
            suggested_cost_per_1k=0.003,
            reason="Switch to cheaper model",
        )
        with pytest.raises(Exception):  # noqa: B017, PT011
            suggestion.agent_id = "bob"  # type: ignore[misc]

    @pytest.mark.unit
    def test_same_model_rejected(self) -> None:
        with pytest.raises(ValueError, match="must differ"):
            RoutingSuggestion(
                agent_id="alice",
                current_model="test-large-001",
                suggested_model="test-large-001",
                current_cost_per_1k=0.09,
                suggested_cost_per_1k=0.003,
                reason="No actual suggestion",
            )

    @pytest.mark.unit
    def test_no_savings_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be less than"):
            RoutingSuggestion(
                agent_id="alice",
                current_model="test-large-001",
                suggested_model="test-small-001",
                current_cost_per_1k=0.003,
                suggested_cost_per_1k=0.09,
                reason="More expensive",
            )


# ── RoutingOptimizationAnalysis Tests ────────────────────────────


class TestRoutingOptimizationAnalysis:
    @pytest.mark.unit
    def test_empty_analysis(self) -> None:
        analysis = RoutingOptimizationAnalysis(
            suggestions=(),
            analysis_period_start=datetime(2026, 2, 1, tzinfo=UTC),
            analysis_period_end=datetime(2026, 3, 1, tzinfo=UTC),
            agents_analyzed=0,
        )
        assert analysis.suggestions == ()
        assert analysis.total_estimated_savings_per_1k == 0.0

    @pytest.mark.unit
    def test_total_savings_is_computed(self) -> None:
        analysis = RoutingOptimizationAnalysis(
            suggestions=(
                RoutingSuggestion(
                    agent_id="alice",
                    current_model="test-large-001",
                    suggested_model="test-small-001",
                    current_cost_per_1k=0.09,
                    suggested_cost_per_1k=0.003,
                    reason="Switch to cheaper",
                ),
                RoutingSuggestion(
                    agent_id="bob",
                    current_model="test-medium-001",
                    suggested_model="test-small-001",
                    current_cost_per_1k=0.03,
                    suggested_cost_per_1k=0.003,
                    reason="Switch to cheaper",
                ),
            ),
            analysis_period_start=datetime(2026, 2, 1, tzinfo=UTC),
            analysis_period_end=datetime(2026, 3, 1, tzinfo=UTC),
            agents_analyzed=2,
        )
        assert analysis.total_estimated_savings_per_1k == 0.114

    @pytest.mark.unit
    def test_period_ordering_invalid(self) -> None:
        with pytest.raises(ValueError, match="analysis_period_start"):
            RoutingOptimizationAnalysis(
                suggestions=(),
                analysis_period_start=datetime(2026, 3, 1, tzinfo=UTC),
                analysis_period_end=datetime(2026, 2, 1, tzinfo=UTC),
                agents_analyzed=0,
            )
