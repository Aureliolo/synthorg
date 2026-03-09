"""Tests for CFO optimizer domain models."""

from datetime import UTC, datetime

import pytest

from ai_company.budget.enums import BudgetAlertLevel
from ai_company.budget.optimizer_models import (
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
            cost_per_1k_tokens=0.05,
            record_count=50,
            efficiency_rating=EfficiencyRating.NORMAL,
        )
        assert eff.agent_id == "alice"
        assert eff.total_cost_usd == 5.0
        assert eff.efficiency_rating == EfficiencyRating.NORMAL

    @pytest.mark.unit
    def test_zero_tokens(self) -> None:
        eff = AgentEfficiency(
            agent_id="alice",
            total_cost_usd=0.0,
            total_tokens=0,
            cost_per_1k_tokens=0.0,
            record_count=0,
            efficiency_rating=EfficiencyRating.NORMAL,
        )
        assert eff.total_tokens == 0
        assert eff.cost_per_1k_tokens == 0.0


# ── EfficiencyAnalysis Tests ─────────────────────────────────────


class TestEfficiencyAnalysis:
    @pytest.mark.unit
    def test_empty_analysis(self) -> None:
        analysis = EfficiencyAnalysis(
            agents=(),
            global_avg_cost_per_1k=0.0,
            analysis_period_start=datetime(2026, 2, 1, tzinfo=UTC),
            analysis_period_end=datetime(2026, 3, 1, tzinfo=UTC),
            inefficient_agent_count=0,
        )
        assert analysis.agents == ()
        assert analysis.inefficient_agent_count == 0

    @pytest.mark.unit
    def test_period_ordering_invalid(self) -> None:
        with pytest.raises(ValueError, match="analysis_period_start"):
            EfficiencyAnalysis(
                agents=(),
                global_avg_cost_per_1k=0.0,
                analysis_period_start=datetime(2026, 3, 1, tzinfo=UTC),
                analysis_period_end=datetime(2026, 2, 1, tzinfo=UTC),
                inefficient_agent_count=0,
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
            total_estimated_monthly_savings=0.0,
            budget_pressure_percent=0.0,
        )
        assert analysis.recommendations == ()
        assert analysis.total_estimated_monthly_savings == 0.0


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
