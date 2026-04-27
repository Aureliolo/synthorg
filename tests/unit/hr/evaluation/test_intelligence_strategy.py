"""Tests for the Intelligence/Accuracy pillar composition."""

import pytest

from synthorg.hr.evaluation.config import EvaluationConfig, IntelligenceConfig
from synthorg.hr.evaluation.configurable_scorer import ConfigurablePillarScorer
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.extractors.intelligence import IntelligenceMetricExtractor
from tests.unit.hr.evaluation.conftest import make_evaluation_context, make_snapshot
from tests.unit.hr.performance.conftest import make_calibration_record

pytestmark = pytest.mark.unit


@pytest.fixture
def strategy() -> ConfigurablePillarScorer:
    return ConfigurablePillarScorer(
        EvaluationPillar.INTELLIGENCE,
        IntelligenceMetricExtractor(),
    )


class TestQualityBlendIntelligenceStrategy:
    """Composed intelligence scorer behaviour tests."""

    def test_protocol_properties(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        assert strategy.name == "configurable[intelligence]"
        assert strategy.pillar == EvaluationPillar.INTELLIGENCE

    async def test_no_quality_score_returns_neutral(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        ctx = make_evaluation_context(
            snapshot=make_snapshot(overall_quality_score=None),
        )
        result = await strategy.score(context=ctx)
        assert result.score == 5.0
        assert result.confidence == 0.0
        assert result.data_point_count == 0

    async def test_ci_quality_only(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        ctx = make_evaluation_context(
            snapshot=make_snapshot(overall_quality_score=8.0),
        )
        result = await strategy.score(context=ctx)
        assert result.pillar == EvaluationPillar.INTELLIGENCE
        assert abs(result.score - 8.0) < 0.1
        assert any(k == "ci_quality" for k, _ in result.breakdown)

    async def test_with_calibration_records(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        records = tuple(
            make_calibration_record(llm_score=8.5, behavioral_score=7.0)
            for _ in range(5)
        )
        ctx = make_evaluation_context(
            snapshot=make_snapshot(overall_quality_score=7.0),
        )
        ctx = ctx.model_copy(update={"calibration_records": records})
        result = await strategy.score(context=ctx)
        # Should blend CI (7.0) with LLM (8.5).
        assert result.score > 7.0
        assert len(result.breakdown) == 2

    async def test_llm_calibration_disabled(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        """With LLM calibration disabled, CI quality gets 100% weight."""
        cfg = EvaluationConfig(
            intelligence=IntelligenceConfig(llm_calibration_enabled=False),
        )
        records = tuple(
            make_calibration_record(llm_score=9.0, behavioral_score=7.0)
            for _ in range(5)
        )
        ctx = make_evaluation_context(
            snapshot=make_snapshot(overall_quality_score=7.0),
            config=cfg,
        )
        ctx = ctx.model_copy(update={"calibration_records": records})
        result = await strategy.score(context=ctx)
        # Should be close to 7.0 (CI only).
        assert abs(result.score - 7.0) < 0.1

    async def test_high_drift_reduces_confidence(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        """High calibration drift should reduce confidence."""
        # Create records with large drift.
        high_drift_records = tuple(
            make_calibration_record(llm_score=9.5, behavioral_score=2.0)
            for _ in range(5)
        )
        low_drift_records = tuple(
            make_calibration_record(llm_score=7.1, behavioral_score=7.0)
            for _ in range(5)
        )
        ctx_high = make_evaluation_context(
            snapshot=make_snapshot(overall_quality_score=7.0),
        )
        ctx_high = ctx_high.model_copy(
            update={"calibration_records": high_drift_records},
        )
        ctx_low = make_evaluation_context(
            snapshot=make_snapshot(overall_quality_score=7.0),
        )
        ctx_low = ctx_low.model_copy(
            update={"calibration_records": low_drift_records},
        )
        result_high = await strategy.score(context=ctx_high)
        result_low = await strategy.score(context=ctx_low)
        assert result_high.confidence < result_low.confidence

    async def test_all_metrics_disabled_returns_neutral(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        cfg = EvaluationConfig(
            intelligence=IntelligenceConfig(
                enabled=False,
                ci_quality_enabled=False,
                llm_calibration_enabled=False,
            ),
        )
        ctx = make_evaluation_context(
            snapshot=make_snapshot(overall_quality_score=8.0),
            config=cfg,
        )
        result = await strategy.score(context=ctx)
        assert result.score == 5.0
        assert result.confidence == 0.0

    async def test_ci_disabled_llm_calibration_only(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        """With CI disabled, LLM calibration gets 100% weight."""
        cfg = EvaluationConfig(
            intelligence=IntelligenceConfig(ci_quality_enabled=False),
        )
        records = tuple(
            make_calibration_record(llm_score=9.0, behavioral_score=7.0)
            for _ in range(5)
        )
        ctx = make_evaluation_context(
            snapshot=make_snapshot(overall_quality_score=7.0),
            config=cfg,
        )
        ctx = ctx.model_copy(update={"calibration_records": records})
        result = await strategy.score(context=ctx)
        assert abs(result.score - 9.0) < 0.1
        assert not any(k == "ci_quality" for k, _ in result.breakdown)

    async def test_ci_disabled_no_calibration_returns_neutral(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        """With CI disabled and no calibration data, return neutral."""
        cfg = EvaluationConfig(
            intelligence=IntelligenceConfig(ci_quality_enabled=False),
        )
        ctx = make_evaluation_context(
            snapshot=make_snapshot(overall_quality_score=8.0),
            config=cfg,
        )
        result = await strategy.score(context=ctx)
        assert result.score == 5.0
        assert result.confidence == 0.0
