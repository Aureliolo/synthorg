"""Tests for the risk scorer module."""

import pytest

from synthorg.security.autonomy.enums import ActionType
from synthorg.security.risk_scorer import (
    DefaultRiskScorer,
    RiskScore,
    RiskScorerWeights,
)


@pytest.mark.unit
class TestRiskScore:
    """Tests for the RiskScore frozen model."""

    def test_construction_valid(self) -> None:
        score = RiskScore(
            reversibility=0.5,
            blast_radius=0.3,
            data_sensitivity=0.2,
            external_visibility=0.1,
        )
        assert score.reversibility == 0.5
        assert score.blast_radius == 0.3
        assert score.data_sensitivity == 0.2
        assert score.external_visibility == 0.1

    def test_frozen(self) -> None:
        score = RiskScore(
            reversibility=0.5,
            blast_radius=0.3,
            data_sensitivity=0.2,
            external_visibility=0.1,
        )
        with pytest.raises(Exception):  # noqa: B017, PT011
            score.reversibility = 0.9  # type: ignore[misc]

    def test_risk_units_default_weights(self) -> None:
        score = RiskScore(
            reversibility=1.0,
            blast_radius=1.0,
            data_sensitivity=1.0,
            external_visibility=1.0,
        )
        # Default weights: 0.3 + 0.3 + 0.2 + 0.2 = 1.0
        assert score.risk_units == pytest.approx(1.0)

    def test_risk_units_zero(self) -> None:
        score = RiskScore(
            reversibility=0.0,
            blast_radius=0.0,
            data_sensitivity=0.0,
            external_visibility=0.0,
        )
        assert score.risk_units == pytest.approx(0.0)

    def test_risk_units_weighted_sum(self) -> None:
        score = RiskScore(
            reversibility=0.5,
            blast_radius=0.0,
            data_sensitivity=1.0,
            external_visibility=0.0,
        )
        # 0.5*0.3 + 0.0*0.3 + 1.0*0.2 + 0.0*0.2 = 0.15 + 0.2 = 0.35
        assert score.risk_units == pytest.approx(0.35)

    def test_risk_units_custom_weights(self) -> None:
        weights = RiskScorerWeights(
            reversibility=0.25,
            blast_radius=0.25,
            data_sensitivity=0.25,
            external_visibility=0.25,
        )
        score = RiskScore(
            reversibility=1.0,
            blast_radius=0.0,
            data_sensitivity=0.0,
            external_visibility=0.0,
            weights=weights,
        )
        assert score.risk_units == pytest.approx(0.25)

    def test_dimension_bounds_lower(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            RiskScore(
                reversibility=-0.1,
                blast_radius=0.0,
                data_sensitivity=0.0,
                external_visibility=0.0,
            )

    def test_dimension_bounds_upper(self) -> None:
        with pytest.raises(ValueError, match="less than or equal to 1"):
            RiskScore(
                reversibility=1.1,
                blast_radius=0.0,
                data_sensitivity=0.0,
                external_visibility=0.0,
            )

    def test_rejects_nan(self) -> None:
        with pytest.raises(ValueError, match=r"finite"):
            RiskScore(
                reversibility=float("nan"),
                blast_radius=0.0,
                data_sensitivity=0.0,
                external_visibility=0.0,
            )

    def test_rejects_inf(self) -> None:
        with pytest.raises(ValueError, match=r"finite"):
            RiskScore(
                reversibility=float("inf"),
                blast_radius=0.0,
                data_sensitivity=0.0,
                external_visibility=0.0,
            )


@pytest.mark.unit
class TestRiskScorerWeights:
    """Tests for RiskScorerWeights validation."""

    def test_default_weights_sum_to_one(self) -> None:
        weights = RiskScorerWeights()
        total = (
            weights.reversibility
            + weights.blast_radius
            + weights.data_sensitivity
            + weights.external_visibility
        )
        assert total == pytest.approx(1.0)

    def test_custom_weights_sum_to_one(self) -> None:
        weights = RiskScorerWeights(
            reversibility=0.1,
            blast_radius=0.2,
            data_sensitivity=0.3,
            external_visibility=0.4,
        )
        total = (
            weights.reversibility
            + weights.blast_radius
            + weights.data_sensitivity
            + weights.external_visibility
        )
        assert total == pytest.approx(1.0)

    def test_weights_not_summing_to_one_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"[Ww]eights must sum to 1\.0"):
            RiskScorerWeights(
                reversibility=0.5,
                blast_radius=0.5,
                data_sensitivity=0.5,
                external_visibility=0.5,
            )

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal to 0"):
            RiskScorerWeights(
                reversibility=-0.1,
                blast_radius=0.4,
                data_sensitivity=0.3,
                external_visibility=0.4,
            )

    def test_frozen(self) -> None:
        weights = RiskScorerWeights()
        with pytest.raises(Exception):  # noqa: B017, PT011
            weights.reversibility = 0.5  # type: ignore[misc]


@pytest.mark.unit
class TestDefaultRiskScorer:
    """Tests for the DefaultRiskScorer implementation."""

    def test_implements_risk_scorer_protocol(self) -> None:
        from synthorg.security.risk_scorer import RiskScorer

        scorer = DefaultRiskScorer()
        assert isinstance(scorer, RiskScorer)

    def test_all_builtin_action_types_have_scores(self) -> None:
        scorer = DefaultRiskScorer()
        for action_type in ActionType:
            score = scorer.score(action_type.value)
            assert isinstance(score, RiskScore)
            assert 0.0 <= score.risk_units <= 1.0

    def test_critical_actions_high_risk(self) -> None:
        scorer = DefaultRiskScorer()
        critical_types = [
            ActionType.DEPLOY_PRODUCTION,
            ActionType.DB_ADMIN,
            ActionType.ORG_FIRE,
        ]
        for action_type in critical_types:
            score = scorer.score(action_type.value)
            assert score.risk_units >= 0.8, (
                f"{action_type} should have risk_units >= 0.8, got {score.risk_units}"
            )

    def test_high_actions_moderate_to_high_risk(self) -> None:
        scorer = DefaultRiskScorer()
        high_types = [
            ActionType.DEPLOY_STAGING,
            ActionType.DB_MUTATE,
            ActionType.CODE_DELETE,
            ActionType.VCS_PUSH,
        ]
        for action_type in high_types:
            score = scorer.score(action_type.value)
            assert score.risk_units >= 0.5, (
                f"{action_type} should have risk_units >= 0.5, got {score.risk_units}"
            )

    def test_low_actions_low_risk(self) -> None:
        scorer = DefaultRiskScorer()
        low_types = [
            ActionType.CODE_READ,
            ActionType.VCS_READ,
            ActionType.DOCS_WRITE,
            ActionType.MEMORY_READ,
        ]
        for action_type in low_types:
            score = scorer.score(action_type.value)
            assert score.risk_units <= 0.15, (
                f"{action_type} should have risk_units <= 0.15, got {score.risk_units}"
            )

    def test_unknown_action_type_high_fallback(self) -> None:
        scorer = DefaultRiskScorer()
        score = scorer.score("unknown:action")
        assert score.risk_units >= 0.7

    def test_custom_score_overrides(self) -> None:
        custom = {
            ActionType.CODE_READ.value: RiskScore(
                reversibility=0.5,
                blast_radius=0.5,
                data_sensitivity=0.5,
                external_visibility=0.5,
            ),
        }
        scorer = DefaultRiskScorer(custom_scores=custom)
        score = scorer.score(ActionType.CODE_READ.value)
        assert score.risk_units == pytest.approx(0.5)

    def test_custom_weights(self) -> None:
        weights = RiskScorerWeights(
            reversibility=0.25,
            blast_radius=0.25,
            data_sensitivity=0.25,
            external_visibility=0.25,
        )
        scorer = DefaultRiskScorer(weights=weights)
        # With equal weights, all-1.0 dimensions should still give 1.0
        score = scorer.score(ActionType.DEPLOY_PRODUCTION.value)
        assert score.risk_units > 0.0

    @pytest.mark.parametrize(
        "action_type",
        list(ActionType),
        ids=[a.name for a in ActionType],
    )
    def test_score_returns_risk_score(self, action_type: ActionType) -> None:
        scorer = DefaultRiskScorer()
        result = scorer.score(action_type.value)
        assert isinstance(result, RiskScore)
        assert 0.0 <= result.reversibility <= 1.0
        assert 0.0 <= result.blast_radius <= 1.0
        assert 0.0 <= result.data_sensitivity <= 1.0
        assert 0.0 <= result.external_visibility <= 1.0
