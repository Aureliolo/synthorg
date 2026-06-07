"""Unit tests for strategy module models."""

import pytest
from pydantic import ValidationError

from synthorg.engine.strategy.models import (
    BlastRadius,
    ConfidenceMetadata,
    ConstitutionalPrinciple,
    CostTierPreset,
    ImpactScore,
    LensAttribution,
    PrinciplePack,
    ProgressiveThresholds,
    ProgressiveWeights,
    Reversibility,
    RiskCard,
    StrategicContext,
    StrategyConfig,
    TimeHorizon,
)
from synthorg.hr.strategy_mode import StrategicOutputMode


class TestStrategicOutputMode:
    """Tests for the StrategicOutputMode enum."""

    @pytest.mark.unit
    def test_all_members_exist(self) -> None:
        assert len(StrategicOutputMode) == 4
        assert StrategicOutputMode.OPTION_EXPANDER.value == "option_expander"
        assert StrategicOutputMode.ADVISOR.value == "advisor"
        assert StrategicOutputMode.DECISION_MAKER.value == "decision_maker"
        assert StrategicOutputMode.CONTEXT_DEPENDENT.value == "context_dependent"


class TestProgressiveWeights:
    """Tests for ProgressiveWeights validation."""

    @pytest.mark.unit
    def test_default_weights_sum_to_one(self) -> None:
        weights = ProgressiveWeights()
        total = (
            weights.budget_impact
            + weights.authority_level
            + weights.decision_type
            + weights.reversibility
            + weights.blast_radius
            + weights.time_horizon
            + weights.strategic_alignment
        )
        assert abs(total - 1.0) < 1e-6

    @pytest.mark.unit
    def test_weights_not_summing_to_one_raises(self) -> None:
        with pytest.raises(ValueError, match=r"must sum to 1\.0"):
            ProgressiveWeights(budget_impact=0.5, authority_level=0.5)

    @pytest.mark.unit
    def test_as_dict_returns_all_dimensions(self) -> None:
        weights = ProgressiveWeights()
        d = weights.as_dict()
        assert len(d) == 7

    @pytest.mark.unit
    def test_frozen(self) -> None:
        weights = ProgressiveWeights()
        with pytest.raises(ValidationError):
            weights.budget_impact = 0.9  # type: ignore[misc]


class TestProgressiveThresholds:
    """Tests for ProgressiveThresholds ordering validation."""

    @pytest.mark.unit
    def test_default_ordering(self) -> None:
        thresholds = ProgressiveThresholds()
        assert thresholds.moderate < thresholds.generous

    @pytest.mark.unit
    def test_moderate_ge_generous_raises(self) -> None:
        with pytest.raises(ValueError, match="must be less than"):
            ProgressiveThresholds(moderate=0.8, generous=0.5)

    @pytest.mark.unit
    def test_equal_thresholds_raises(self) -> None:
        with pytest.raises(ValueError, match="must be less than"):
            ProgressiveThresholds(moderate=0.5, generous=0.5)


class TestStrategyConfig:
    """Tests for StrategyConfig top-level config model."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        config = StrategyConfig()
        assert config.output_mode == StrategicOutputMode.ADVISOR
        assert config.cost_tier == CostTierPreset.MODERATE
        assert len(config.default_lenses) == 4
        assert config.constitutional_principles.pack == "default"

    @pytest.mark.unit
    def test_empty_lenses_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one lens"):
            StrategyConfig(default_lenses=())

    @pytest.mark.unit
    def test_frozen(self) -> None:
        config = StrategyConfig()
        with pytest.raises(ValidationError):
            config.output_mode = StrategicOutputMode.DECISION_MAKER  # type: ignore[misc]

    @pytest.mark.unit
    def test_custom_lenses(self) -> None:
        config = StrategyConfig(default_lenses=("contrarian",))
        assert config.default_lenses == ("contrarian",)


class TestStrategicContext:
    """Tests for StrategicContext runtime model."""

    @pytest.mark.unit
    def test_creation(self) -> None:
        ctx = StrategicContext(
            maturity_stage="growth",
            industry="technology",
            competitive_position="challenger",
        )
        assert ctx.maturity_stage == "growth"
        assert ctx.industry == "technology"
        assert ctx.competitive_position == "challenger"

    @pytest.mark.unit
    def test_frozen(self) -> None:
        ctx = StrategicContext(
            maturity_stage="growth",
            industry="tech",
            competitive_position="leader",
        )
        with pytest.raises(ValidationError):
            ctx.maturity_stage = "mature"  # type: ignore[misc]


class TestConstitutionalPrinciple:
    """Tests for ConstitutionalPrinciple model."""

    @pytest.mark.unit
    def test_creation(self) -> None:
        p = ConstitutionalPrinciple(id="test", text="Test rule")
        assert p.id == "test"
        assert p.text == "Test rule"
        assert p.category == "anti_trendslop"
        assert p.severity.value == "warning"


class TestPrinciplePack:
    """Tests for PrinciplePack model."""

    @pytest.mark.unit
    def test_unique_ids(self) -> None:
        pack = PrinciplePack(
            name="test",
            version="1.0.0",
            principles=(
                ConstitutionalPrinciple(id="a", text="Rule A"),
                ConstitutionalPrinciple(id="b", text="Rule B"),
            ),
        )
        assert len(pack.principles) == 2

    @pytest.mark.unit
    def test_duplicate_ids_raises(self) -> None:
        with pytest.raises(ValueError, match="Duplicate principle IDs"):
            PrinciplePack(
                name="test",
                version="1.0.0",
                principles=(
                    ConstitutionalPrinciple(id="a", text="Rule A"),
                    ConstitutionalPrinciple(id="a", text="Rule B"),
                ),
            )


class TestRiskCard:
    """Tests for RiskCard model."""

    @pytest.mark.unit
    def test_defaults(self) -> None:
        card = RiskCard(decision_type="test")
        assert card.reversibility == Reversibility.MODERATE
        assert card.blast_radius == BlastRadius.TEAM
        assert card.time_horizon == TimeHorizon.MEDIUM_TERM


class TestImpactScore:
    """Tests for ImpactScore model."""

    @pytest.mark.unit
    def test_valid_creation(self) -> None:
        score = ImpactScore(
            dimensions={"budget_impact": 0.5, "reversibility": 0.8},
            composite=0.65,
            tier=CostTierPreset.MODERATE,
        )
        assert score.composite == 0.65

    @pytest.mark.unit
    def test_dimension_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="must be in"):
            ImpactScore(
                dimensions={"budget_impact": 1.5},
                composite=0.5,
                tier=CostTierPreset.MODERATE,
            )

    @pytest.mark.unit
    def test_composite_out_of_range_raises(self) -> None:
        with pytest.raises(ValidationError, match="less than or equal"):
            ImpactScore(
                dimensions={},
                composite=1.5,
                tier=CostTierPreset.MODERATE,
            )


class TestConfidenceMetadata:
    """Tests for ConfidenceMetadata model."""

    @pytest.mark.unit
    def test_valid_range(self) -> None:
        meta = ConfidenceMetadata(
            level=0.7,
            range_lower=0.5,
            range_upper=0.9,
        )
        assert meta.level == 0.7

    @pytest.mark.unit
    def test_lower_exceeds_level_raises(self) -> None:
        with pytest.raises(ValueError, match="range_lower"):
            ConfidenceMetadata(
                level=0.5,
                range_lower=0.8,
                range_upper=0.9,
            )

    @pytest.mark.unit
    def test_level_exceeds_upper_raises(self) -> None:
        with pytest.raises(ValueError, match="range_upper"):
            ConfidenceMetadata(
                level=0.9,
                range_lower=0.5,
                range_upper=0.7,
            )


class TestLensAttribution:
    """Tests for LensAttribution model."""

    @pytest.mark.unit
    def test_creation(self) -> None:
        attr = LensAttribution(
            lens="contrarian",
            insight="The opposite approach has merit",
            weight=0.3,
        )
        assert attr.lens == "contrarian"
        assert attr.weight == 0.3
