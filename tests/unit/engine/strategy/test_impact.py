"""Unit tests for impact scoring."""

import pytest

from synthorg.engine.strategy.impact import (
    CompositeImpactScorer,
    ExplicitImpactScorer,
    HybridImpactScorer,
)
from synthorg.engine.strategy.models import (
    BlastRadius,
    CostTierPreset,
    ImpactDimension,
    ProgressiveConfig,
    ProgressiveThresholds,
    Reversibility,
    RiskCard,
    StrategicContext,
    TimeHorizon,
)


class TestCompositeImpactScorer:
    """Tests for CompositeImpactScorer."""

    @pytest.mark.unit
    def test_score_returns_impact_score(
        self,
        strategic_context: StrategicContext,
        risk_card: RiskCard,
        progressive_config: ProgressiveConfig,
    ) -> None:
        scorer = CompositeImpactScorer()
        result = scorer.score(
            context=strategic_context,
            risk_card=risk_card,
            config=progressive_config,
        )
        assert 0.0 <= result.composite <= 1.0
        assert result.tier in CostTierPreset

    @pytest.mark.unit
    def test_high_risk_card_scores_higher(
        self,
        strategic_context: StrategicContext,
        progressive_config: ProgressiveConfig,
    ) -> None:
        scorer = CompositeImpactScorer()
        low_risk = RiskCard(
            decision_type="minor",
            reversibility=Reversibility.EASILY_REVERSIBLE,
            blast_radius=BlastRadius.INDIVIDUAL,
            time_horizon=TimeHorizon.IMMEDIATE,
        )
        high_risk = RiskCard(
            decision_type="major",
            reversibility=Reversibility.LOCKED_IN,
            blast_radius=BlastRadius.COMPANY_WIDE,
            time_horizon=TimeHorizon.LONG_TERM,
        )
        low_score = scorer.score(
            context=strategic_context,
            risk_card=low_risk,
            config=progressive_config,
        )
        high_score = scorer.score(
            context=strategic_context,
            risk_card=high_risk,
            config=progressive_config,
        )
        assert high_score.composite > low_score.composite

    @pytest.mark.unit
    def test_all_dimensions_present(
        self,
        strategic_context: StrategicContext,
        risk_card: RiskCard,
        progressive_config: ProgressiveConfig,
    ) -> None:
        scorer = CompositeImpactScorer()
        result = scorer.score(
            context=strategic_context,
            risk_card=risk_card,
            config=progressive_config,
        )
        for dim in ImpactDimension:
            assert dim.value in result.dimensions


class TestExplicitImpactScorer:
    """Tests for ExplicitImpactScorer."""

    @pytest.mark.unit
    def test_explicit_dimensions(
        self,
        strategic_context: StrategicContext,
        risk_card: RiskCard,
        progressive_config: ProgressiveConfig,
    ) -> None:
        scorer = ExplicitImpactScorer(
            explicit_dimensions={
                ImpactDimension.BUDGET_IMPACT.value: 0.9,
                ImpactDimension.REVERSIBILITY.value: 0.1,
            },
        )
        result = scorer.score(
            context=strategic_context,
            risk_card=risk_card,
            config=progressive_config,
        )
        assert 0.0 <= result.composite <= 1.0


class TestHybridImpactScorer:
    """Tests for HybridImpactScorer."""

    @pytest.mark.unit
    def test_merges_explicit_with_composite(
        self,
        strategic_context: StrategicContext,
        risk_card: RiskCard,
        progressive_config: ProgressiveConfig,
    ) -> None:
        scorer = HybridImpactScorer(
            explicit_dimensions={
                ImpactDimension.BUDGET_IMPACT.value: 1.0,
            },
        )
        result = scorer.score(
            context=strategic_context,
            risk_card=risk_card,
            config=progressive_config,
        )
        assert result.dimensions[ImpactDimension.BUDGET_IMPACT.value] == 1.0
        assert 0.0 <= result.composite <= 1.0

    @pytest.mark.unit
    def test_explicit_dimensions_copied_defensively(
        self,
        strategic_context: StrategicContext,
        risk_card: RiskCard,
        progressive_config: ProgressiveConfig,
    ) -> None:
        """Mutating the caller's dict after construction must not leak in."""
        explicit = {ImpactDimension.BUDGET_IMPACT.value: 1.0}
        scorer = HybridImpactScorer(explicit_dimensions=explicit)
        explicit[ImpactDimension.BUDGET_IMPACT.value] = 0.0

        result = scorer.score(
            context=strategic_context,
            risk_card=risk_card,
            config=progressive_config,
        )

        assert result.dimensions[ImpactDimension.BUDGET_IMPACT.value] == 1.0


class TestTierResolution:
    """Tests for tier resolution within impact scoring."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("moderate", "generous", "composite", "expected_tier"),
        [
            (0.4, 0.7, 0.1, CostTierPreset.MINIMAL),
            (0.4, 0.7, 0.5, CostTierPreset.MODERATE),
            (0.4, 0.7, 0.8, CostTierPreset.GENEROUS),
            (0.4, 0.7, 0.0, CostTierPreset.MINIMAL),
            (0.4, 0.7, 0.4, CostTierPreset.MODERATE),
            (0.4, 0.7, 0.7, CostTierPreset.GENEROUS),
        ],
    )
    def test_threshold_boundaries(
        self,
        moderate: float,
        generous: float,
        composite: float,
        expected_tier: CostTierPreset,
    ) -> None:
        from synthorg.engine.strategy.impact import _resolve_tier

        config = ProgressiveConfig(
            thresholds=ProgressiveThresholds(
                moderate=moderate,
                generous=generous,
            ),
        )
        assert _resolve_tier(composite, config) == expected_tier


class TestNormalizationMapExhaustiveness:
    """Verify normalization maps cover all enum variants."""

    @pytest.mark.unit
    def test_reversibility_map_covers_all_variants(self) -> None:
        from synthorg.engine.strategy.impact import _REVERSIBILITY_SCORES

        for variant in Reversibility:
            assert variant.value in _REVERSIBILITY_SCORES, (
                f"Missing {variant.value} in _REVERSIBILITY_SCORES"
            )

    @pytest.mark.unit
    def test_blast_radius_map_covers_all_variants(self) -> None:
        from synthorg.engine.strategy.impact import _BLAST_RADIUS_SCORES

        for variant in BlastRadius:
            assert variant.value in _BLAST_RADIUS_SCORES, (
                f"Missing {variant.value} in _BLAST_RADIUS_SCORES"
            )

    @pytest.mark.unit
    def test_time_horizon_map_covers_all_variants(self) -> None:
        from synthorg.engine.strategy.impact import _TIME_HORIZON_SCORES

        for variant in TimeHorizon:
            assert variant.value in _TIME_HORIZON_SCORES, (
                f"Missing {variant.value} in _TIME_HORIZON_SCORES"
            )
