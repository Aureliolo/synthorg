"""Unit tests for OrgInflectionDetector."""

import pytest

from synthorg.meta.chief_of_staff.inflection import OrgInflectionDetector
from synthorg.meta.models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    RuleSeverity,
)

pytestmark = pytest.mark.unit


def _snap(
    *,
    quality: float = 7.5,
    success: float = 0.85,
    spend: float = 150.0,
    orch_overhead: float = 0.5,
    error_findings: int = 0,
) -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=quality,
            avg_success_rate=success,
            avg_collaboration_score=6.0,
            agent_count=10,
        ),
        budget=OrgBudgetSummary(
            total_spend=spend,
            productive_ratio=0.6,
            coordination_ratio=0.3,
            system_ratio=0.1,
            forecast_confidence=0.8,
            orchestration_overhead=orch_overhead,
        ),
        coordination=OrgCoordinationSummary(),
        errors=OrgErrorSummary(total_findings=error_findings),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


class TestOrgInflectionDetector:
    """OrgInflectionDetector tests."""

    async def test_no_change_no_inflection(self) -> None:
        detector = OrgInflectionDetector()
        prev = _snap()
        curr = _snap()
        result = await detector.detect(previous=prev, current=curr)
        assert result == ()

    async def test_small_change_below_threshold(self) -> None:
        detector = OrgInflectionDetector(warning_threshold=0.15)
        prev = _snap(quality=7.5)
        curr = _snap(quality=7.0)  # ~6.7% change
        result = await detector.detect(previous=prev, current=curr)
        # quality change is below 15%
        quality_inflections = [i for i in result if i.metric_name == "quality_score"]
        assert len(quality_inflections) == 0

    async def test_warning_level_change(self) -> None:
        detector = OrgInflectionDetector(
            warning_threshold=0.10,
            critical_threshold=0.30,
        )
        prev = _snap(quality=7.5)
        curr = _snap(quality=6.0)  # 20% drop
        result = await detector.detect(previous=prev, current=curr)
        quality = [i for i in result if i.metric_name == "quality_score"]
        assert len(quality) == 1
        assert quality[0].severity is RuleSeverity.WARNING
        assert quality[0].old_value == pytest.approx(7.5)
        assert quality[0].new_value == pytest.approx(6.0)

    async def test_critical_level_change(self) -> None:
        detector = OrgInflectionDetector(
            warning_threshold=0.10,
            critical_threshold=0.30,
        )
        prev = _snap(quality=7.5)
        curr = _snap(quality=4.0)  # ~47% drop
        result = await detector.detect(previous=prev, current=curr)
        quality = [i for i in result if i.metric_name == "quality_score"]
        assert len(quality) == 1
        assert quality[0].severity is RuleSeverity.CRITICAL

    async def test_multiple_metrics_detected(self) -> None:
        detector = OrgInflectionDetector(
            warning_threshold=0.10,
            critical_threshold=0.50,
        )
        prev = _snap(quality=7.5, success=0.85, spend=100.0)
        curr = _snap(quality=5.0, success=0.60, spend=200.0)
        result = await detector.detect(previous=prev, current=curr)
        metric_names = {i.metric_name for i in result}
        assert "quality_score" in metric_names
        assert "success_rate" in metric_names
        assert "total_spend" in metric_names

    async def test_affected_domains_set(self) -> None:
        detector = OrgInflectionDetector(warning_threshold=0.10)
        prev = _snap(quality=7.5)
        curr = _snap(quality=5.0)
        result = await detector.detect(previous=prev, current=curr)
        quality = [i for i in result if i.metric_name == "quality_score"]
        assert quality[0].affected_domains == ("performance",)

    async def test_both_zero_no_inflection(self) -> None:
        detector = OrgInflectionDetector()
        prev = _snap(error_findings=0)
        curr = _snap(error_findings=0)
        result = await detector.detect(previous=prev, current=curr)
        error_inflections = [
            i for i in result if i.metric_name == "total_error_findings"
        ]
        assert len(error_inflections) == 0
