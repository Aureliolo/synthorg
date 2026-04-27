"""Tests for the Responsibility/Governance pillar composition."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.config import EvaluationConfig, GovernanceConfig
from synthorg.hr.evaluation.configurable_scorer import ConfigurablePillarScorer
from synthorg.hr.evaluation.enums import EvaluationPillar
from synthorg.hr.evaluation.extractors.governance import GovernanceMetricExtractor
from tests.unit.hr.evaluation.conftest import make_evaluation_context

pytestmark = pytest.mark.unit


@pytest.fixture
def strategy() -> ConfigurablePillarScorer:
    return ConfigurablePillarScorer(
        EvaluationPillar.GOVERNANCE,
        GovernanceMetricExtractor(),
    )


class TestAuditBasedGovernanceStrategy:
    """Composed governance scorer behaviour tests."""

    def test_protocol_properties(self, strategy: ConfigurablePillarScorer) -> None:
        assert strategy.name == "configurable[governance]"
        assert strategy.pillar == EvaluationPillar.GOVERNANCE

    async def test_no_data_returns_neutral(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        """No audit data, no trust level, autonomy disabled -> neutral."""
        cfg = EvaluationConfig(
            governance=GovernanceConfig(autonomy_compliance_enabled=False),
        )
        ctx = make_evaluation_context(config=cfg)
        result = await strategy.score(context=ctx)
        assert result.score == 5.0
        assert result.confidence == 0.0

    async def test_all_allows(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        ctx = make_evaluation_context()
        ctx = ctx.model_copy(
            update={
                "audit_allow_count": 50,
                "audit_deny_count": 0,
                "audit_escalate_count": 0,
                "audit_high_risk_count": 0,
                "trust_level": NotBlankStr("elevated"),
                "autonomy_downgrades_in_window": 0,
            }
        )
        result = await strategy.score(context=ctx)
        assert result.score >= 9.0
        assert result.confidence > 0.0

    async def test_all_denies(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        ctx = make_evaluation_context()
        ctx = ctx.model_copy(
            update={
                "audit_allow_count": 0,
                "audit_deny_count": 20,
                "audit_escalate_count": 0,
                "audit_high_risk_count": 5,
                "trust_level": NotBlankStr("sandboxed"),
                "autonomy_downgrades_in_window": 3,
            }
        )
        result = await strategy.score(context=ctx)
        assert result.score < 3.0

    async def test_mixed_verdicts(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        ctx = make_evaluation_context()
        ctx = ctx.model_copy(
            update={
                "audit_allow_count": 40,
                "audit_deny_count": 5,
                "audit_escalate_count": 5,
                "audit_high_risk_count": 2,
                "trust_level": NotBlankStr("standard"),
                "autonomy_downgrades_in_window": 1,
            }
        )
        result = await strategy.score(context=ctx)
        assert 3.0 < result.score < 9.0

    async def test_audit_compliance_disabled(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        cfg = EvaluationConfig(
            governance=GovernanceConfig(audit_compliance_enabled=False),
        )
        ctx = make_evaluation_context(config=cfg)
        ctx = ctx.model_copy(
            update={
                "audit_allow_count": 50,
                "trust_level": NotBlankStr("standard"),
            }
        )
        result = await strategy.score(context=ctx)
        assert not any(k == "audit_compliance" for k, _ in result.breakdown)

    async def test_trust_level_disabled(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        cfg = EvaluationConfig(
            governance=GovernanceConfig(trust_level_enabled=False),
        )
        ctx = make_evaluation_context(config=cfg)
        ctx = ctx.model_copy(
            update={
                "audit_allow_count": 30,
                "trust_level": NotBlankStr("elevated"),
            }
        )
        result = await strategy.score(context=ctx)
        assert not any(k == "trust_level" for k, _ in result.breakdown)

    async def test_trust_demotions_penalize(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        ctx_no_demotions = make_evaluation_context()
        ctx_no_demotions = ctx_no_demotions.model_copy(
            update={
                "trust_level": NotBlankStr("standard"),
                "trust_demotions_in_window": 0,
                "audit_allow_count": 10,
            }
        )
        ctx_demotions = make_evaluation_context()
        ctx_demotions = ctx_demotions.model_copy(
            update={
                "trust_level": NotBlankStr("standard"),
                "trust_demotions_in_window": 2,
                "audit_allow_count": 10,
            }
        )
        result_clean = await strategy.score(context=ctx_no_demotions)
        result_demoted = await strategy.score(context=ctx_demotions)
        assert result_demoted.score < result_clean.score

    async def test_autonomy_downgrades_penalize(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        ctx_clean = make_evaluation_context()
        ctx_clean = ctx_clean.model_copy(
            update={
                "trust_level": NotBlankStr("standard"),
                "autonomy_downgrades_in_window": 0,
                "audit_allow_count": 10,
            }
        )
        ctx_downgraded = make_evaluation_context()
        ctx_downgraded = ctx_downgraded.model_copy(
            update={
                "trust_level": NotBlankStr("standard"),
                "autonomy_downgrades_in_window": 3,
                "audit_allow_count": 10,
            }
        )
        result_clean = await strategy.score(context=ctx_clean)
        result_downgraded = await strategy.score(context=ctx_downgraded)
        assert result_downgraded.score < result_clean.score

    async def test_unknown_trust_level_falls_back_to_neutral(
        self,
        strategy: ConfigurablePillarScorer,
    ) -> None:
        """Unrecognized trust level maps to neutral score (5.0)."""
        ctx = make_evaluation_context()
        ctx = ctx.model_copy(
            update={
                "trust_level": NotBlankStr("unknown_custom_level"),
                "audit_allow_count": 10,
            }
        )
        result = await strategy.score(context=ctx)
        # Trust component should be near neutral (5.0).
        trust_scores = [v for k, v in result.breakdown if k == "trust_level"]
        assert len(trust_scores) == 1
        assert trust_scores[0] == 5.0
