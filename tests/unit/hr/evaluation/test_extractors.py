"""Direct unit tests for the per-pillar ``MetricExtractor`` implementations.

These tests exercise each extractor's ``extract()`` method in
isolation, separate from the composed ``ConfigurablePillarScorer``
path. The composite tests (``test_*_strategy.py``) cover end-to-end
behaviour; these cover the extractor's data-extraction contract.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.config import (
    EvaluationConfig,
    GovernanceConfig,
    ResilienceConfig,
)
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.extractors.efficiency import EfficiencyMetricExtractor
from synthorg.hr.evaluation.extractors.experience import ExperienceMetricExtractor
from synthorg.hr.evaluation.extractors.governance import GovernanceMetricExtractor
from synthorg.hr.evaluation.extractors.intelligence import (
    IntelligenceMetricExtractor,
)
from synthorg.hr.evaluation.extractors.resilience import ResilienceMetricExtractor
from synthorg.hr.evaluation.metric_extractor_protocol import MetricExtractor
from synthorg.hr.evaluation.models import EvaluationContext
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    LlmCalibrationRecord,
    WindowMetrics,
)

from .conftest import (
    make_evaluation_context,
    make_interaction_feedback,
    make_resilience_metrics,
    make_snapshot,
)

pytestmark = pytest.mark.unit


def _ctx(**overrides: object) -> EvaluationContext:
    return make_evaluation_context().model_copy(update=overrides)


class TestExtractorProtocolConformance:
    """All five extractors satisfy the ``MetricExtractor`` Protocol."""

    @pytest.mark.parametrize(
        ("extractor", "expected_pillar"),
        [
            (IntelligenceMetricExtractor(), EvaluationPillar.INTELLIGENCE),
            (EfficiencyMetricExtractor(), EvaluationPillar.EFFICIENCY),
            (ResilienceMetricExtractor(), EvaluationPillar.RESILIENCE),
            (GovernanceMetricExtractor(), EvaluationPillar.GOVERNANCE),
            (ExperienceMetricExtractor(), EvaluationPillar.EXPERIENCE),
        ],
    )
    def test_protocol_and_pillar(
        self,
        extractor: MetricExtractor,
        expected_pillar: EvaluationPillar,
    ) -> None:
        assert isinstance(extractor, MetricExtractor)
        assert extractor.pillar is expected_pillar


class TestResilienceMetricExtractor:
    """``ResilienceMetricExtractor.extract()`` behaviour."""

    async def test_no_resilience_metrics_signals_insufficient(self) -> None:
        result = await ResilienceMetricExtractor().extract(_ctx())
        assert result.insufficient_data is True
        assert result.insufficient_data_event_kwargs == {
            "reason": "no_resilience_metrics",
        }

    async def test_emits_all_4_metrics_with_default_config(self) -> None:
        rm = make_resilience_metrics(
            total_tasks=20,
            failed_tasks=4,
            recovered_tasks=2,
            current_success_streak=5,
            longest_success_streak=10,
            quality_score_stddev=1.0,
        )
        result = await ResilienceMetricExtractor().extract(
            _ctx(resilience_metrics=rm),
        )
        assert result.insufficient_data is False
        # All 4 metrics enabled by default.
        assert set(result.scores.keys()) == {
            "success_rate",
            "recovery_rate",
            "consistency",
            "streak",
        }
        assert set(result.weights.keys()) == set(result.scores.keys())
        assert result.data_points == 20

    async def test_respects_metric_disable(self) -> None:
        rm = make_resilience_metrics()
        cfg = EvaluationConfig(
            resilience=ResilienceConfig(consistency_enabled=False),
        )
        result = await ResilienceMetricExtractor().extract(
            _ctx(config=cfg, resilience_metrics=rm),
        )
        assert "consistency" not in result.scores
        assert "consistency" not in result.weights

    async def test_no_failures_perfect_recovery(self) -> None:
        # Quirk preserved: 0 failures => recovery rate = 1.0.
        rm = make_resilience_metrics(
            total_tasks=10,
            failed_tasks=0,
            recovered_tasks=0,
        )
        result = await ResilienceMetricExtractor().extract(
            _ctx(resilience_metrics=rm),
        )
        assert result.scores["recovery_rate"] == 10.0


class TestGovernanceMetricExtractor:
    """``GovernanceMetricExtractor.extract()`` behaviour."""

    async def test_no_governance_data_signals_insufficient(self) -> None:
        cfg = EvaluationConfig(
            governance=GovernanceConfig(autonomy_compliance_enabled=False),
        )
        result = await GovernanceMetricExtractor().extract(_ctx(config=cfg))
        assert result.insufficient_data is True
        assert result.insufficient_data_event_kwargs == {
            "reason": "no_governance_data",
        }

    async def test_audit_compliance_only(self) -> None:
        cfg = EvaluationConfig(
            governance=GovernanceConfig(
                trust_level_enabled=False,
                autonomy_compliance_enabled=False,
            ),
        )
        result = await GovernanceMetricExtractor().extract(
            _ctx(config=cfg, audit_allow_count=10, audit_deny_count=2),
        )
        assert "audit_compliance" in result.scores
        assert "trust_level" not in result.scores
        assert "autonomy_compliance" not in result.scores
        assert result.data_points == 12  # total audits

    async def test_unknown_trust_level_falls_back_to_neutral(self) -> None:
        result = await GovernanceMetricExtractor().extract(
            _ctx(trust_level=NotBlankStr("nonexistent-level")),
        )
        # Trust level emitted with NEUTRAL_SCORE (5.0), not crashed.
        assert "trust_level" in result.scores
        assert result.scores["trust_level"] == 5.0


class TestExperienceMetricExtractor:
    """``ExperienceMetricExtractor.extract()`` behaviour."""

    async def test_below_min_feedback_signals_insufficient(self) -> None:
        # Only 1 feedback record; default min is 3.
        fb = (make_interaction_feedback(),)
        result = await ExperienceMetricExtractor().extract(_ctx(feedback=fb))
        assert result.insufficient_data is True
        assert (
            result.insufficient_data_event_kwargs["reason"] == "insufficient_feedback"
        )
        # The neutral_data_point_count quirk: preserves the count.
        assert result.neutral_data_point_count == 1

    async def test_aggregates_5_dimensions(self) -> None:
        fb = tuple(make_interaction_feedback() for _ in range(3))
        result = await ExperienceMetricExtractor().extract(_ctx(feedback=fb))
        assert set(result.scores.keys()) == {
            "clarity",
            "tone",
            "helpfulness",
            "trust",
            "satisfaction",
        }

    async def test_custom_confidence_multiplier_correct(self) -> None:
        # The math: with default min_feedback_count=3, full saturation
        # at min*3=9 feedback records. multiplier = 10 / (3*3) = 10/9.
        fb = tuple(make_interaction_feedback() for _ in range(3))
        result = await ExperienceMetricExtractor().extract(_ctx(feedback=fb))
        assert result.confidence_multiplier == pytest.approx(10.0 / 9.0)


class TestIntelligenceMetricExtractor:
    """``IntelligenceMetricExtractor.extract()`` behaviour."""

    async def test_no_metrics_signals_insufficient(self) -> None:
        # No quality score, no calibration records.
        result = await IntelligenceMetricExtractor().extract(
            _ctx(snapshot=make_snapshot(overall_quality_score=None)),
        )
        assert result.insufficient_data is True
        assert result.insufficient_data_event_kwargs == {
            "reason": "no_enabled_metrics_with_data",
        }

    async def test_ci_quality_only(self) -> None:
        result = await IntelligenceMetricExtractor().extract(_ctx())
        assert "ci_quality" in result.scores
        assert "llm_calibration" not in result.scores
        assert result.confidence_multiplier == 1.0

    async def test_drift_above_threshold_reduces_confidence(self) -> None:
        # Build calibration records with high drift via the
        # @computed_field for `drift`. We pass the LLM and behavioral
        # scores; the model derives drift from |llm - behavioral|.
        records = (
            LlmCalibrationRecord(
                agent_id=NotBlankStr("agent-001"),
                sampled_at=datetime.now(UTC),
                interaction_record_id=NotBlankStr("interaction-1"),
                llm_score=8.0,
                behavioral_score=5.0,
                rationale=NotBlankStr("test"),
                model_used=NotBlankStr("test-model-001"),
                cost=0.01,
                currency="USD",
            ),
        )
        # Default threshold is 2.0; drift 3.0 should engage penalty.
        result = await IntelligenceMetricExtractor().extract(
            _ctx(calibration_records=records),
        )
        assert result.confidence_multiplier < 1.0
        # Floor is 0.1 per the formula.
        assert result.confidence_multiplier >= 0.1


class TestEfficiencyMetricExtractor:
    """``EfficiencyMetricExtractor.extract()`` behaviour."""

    def _snapshot_with_window(
        self,
        *,
        window_size: str = "30d",
        avg_cost: float | None = 5.0,
        avg_time: float | None = 60.0,
        avg_tokens: float | None = 1000.0,
        data_point_count: int = 10,
    ) -> AgentPerformanceSnapshot:
        window = WindowMetrics(
            window_size=NotBlankStr(window_size),
            data_point_count=data_point_count,
            tasks_completed=data_point_count,
            tasks_failed=0,
            avg_quality_score=7.0,
            avg_cost_per_task=avg_cost,
            avg_completion_time_seconds=avg_time,
            avg_tokens_per_task=avg_tokens,
            success_rate=1.0,
            currency="USD",
        )
        return make_snapshot(windows=(window,))

    async def test_no_window_signals_insufficient(self) -> None:
        snapshot = make_snapshot(windows=())
        result = await EfficiencyMetricExtractor().extract(_ctx(snapshot=snapshot))
        assert result.insufficient_data is True
        assert result.insufficient_data_event_kwargs == {"reason": "no_window_data"}

    async def test_uses_30d_when_present(self) -> None:
        snapshot = self._snapshot_with_window(window_size="30d")
        result = await EfficiencyMetricExtractor().extract(_ctx(snapshot=snapshot))
        assert result.insufficient_data is False
        assert "cost" in result.scores
        assert "time" in result.scores
        assert "tokens" in result.scores

    async def test_falls_back_to_7d_when_30d_missing(self) -> None:
        snapshot = self._snapshot_with_window(window_size="7d")
        result = await EfficiencyMetricExtractor().extract(_ctx(snapshot=snapshot))
        assert result.insufficient_data is False
        assert result.data_points == 10

    async def test_resolver_disables_cost(self) -> None:
        # Stubbed resolver returns False for hr.evaluation_cost_enabled.
        from unittest.mock import AsyncMock

        resolver: Any = AsyncMock()

        async def _get_bool(namespace: str, key: str) -> bool:
            return not (namespace == "hr" and key == "evaluation_cost_enabled")

        resolver.get_bool = AsyncMock(side_effect=_get_bool)
        snapshot = self._snapshot_with_window()
        extractor = EfficiencyMetricExtractor(config_resolver=resolver)
        result = await extractor.extract(_ctx(snapshot=snapshot))
        assert "cost" not in result.scores
        assert "time" in result.scores
        assert "tokens" in result.scores

    async def test_zero_data_point_window_signals_insufficient(self) -> None:
        snapshot = self._snapshot_with_window(data_point_count=0)
        result = await EfficiencyMetricExtractor().extract(_ctx(snapshot=snapshot))
        assert result.insufficient_data is True
