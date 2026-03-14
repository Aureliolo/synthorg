"""Unit tests for SeniorityApprovalStrategy."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import SeniorityLevel
from synthorg.hr.enums import PromotionDirection
from synthorg.hr.promotion.config import PromotionApprovalConfig
from synthorg.hr.promotion.models import PromotionEvaluation
from synthorg.hr.promotion.seniority_approval_strategy import (
    SeniorityApprovalStrategy,
)

from .conftest import make_agent_identity

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


def _make_evaluation(
    *,
    current_level: SeniorityLevel,
    target_level: SeniorityLevel,
    direction: PromotionDirection,
) -> PromotionEvaluation:
    """Build a minimal PromotionEvaluation for testing."""
    return PromotionEvaluation(
        agent_id="agent-001",
        current_level=current_level,
        target_level=target_level,
        direction=direction,
        criteria_results=(),
        required_criteria_met=True,
        eligible=True,
        evaluated_at=datetime.now(UTC),
        strategy_name="threshold_evaluator",
    )


@pytest.mark.unit
class TestSeniorityApprovalStrategyName:
    """Tests for strategy identity."""

    def test_name(
        self,
        approval_config: PromotionApprovalConfig,
    ) -> None:
        """Strategy name is 'seniority_approval'."""
        strategy = SeniorityApprovalStrategy(config=approval_config)
        assert strategy.name == "seniority_approval"


@pytest.mark.unit
class TestSeniorityApprovalPromotion:
    """Tests for promotion approval decisions."""

    async def test_junior_to_mid_auto_approves(
        self,
        approval_config: PromotionApprovalConfig,
    ) -> None:
        """Junior to Mid promotion is auto-approved (below SENIOR threshold)."""
        strategy = SeniorityApprovalStrategy(config=approval_config)
        evaluation = _make_evaluation(
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.PROMOTION,
        )
        identity = make_agent_identity(level=SeniorityLevel.JUNIOR)

        decision = await strategy.decide(
            evaluation=evaluation,
            agent_identity=identity,
        )
        assert decision.auto_approve is True
        assert decision.requires_human is False

    async def test_mid_to_senior_requires_human(
        self,
        approval_config: PromotionApprovalConfig,
    ) -> None:
        """Mid to Senior promotion requires human approval (at threshold)."""
        strategy = SeniorityApprovalStrategy(config=approval_config)
        evaluation = _make_evaluation(
            current_level=SeniorityLevel.MID,
            target_level=SeniorityLevel.SENIOR,
            direction=PromotionDirection.PROMOTION,
        )
        identity = make_agent_identity(level=SeniorityLevel.MID)

        decision = await strategy.decide(
            evaluation=evaluation,
            agent_identity=identity,
        )
        assert decision.auto_approve is False
        assert decision.requires_human is True

    async def test_senior_to_lead_requires_human(
        self,
        approval_config: PromotionApprovalConfig,
    ) -> None:
        """Senior to Lead promotion also requires human approval."""
        strategy = SeniorityApprovalStrategy(config=approval_config)
        evaluation = _make_evaluation(
            current_level=SeniorityLevel.SENIOR,
            target_level=SeniorityLevel.LEAD,
            direction=PromotionDirection.PROMOTION,
        )
        identity = make_agent_identity(level=SeniorityLevel.SENIOR)

        decision = await strategy.decide(
            evaluation=evaluation,
            agent_identity=identity,
        )
        assert decision.auto_approve is False
        assert decision.requires_human is True

    async def test_custom_threshold_lead(self) -> None:
        """Promotion to Senior auto-approved when threshold is LEAD."""
        config = PromotionApprovalConfig(
            human_approval_from_level=SeniorityLevel.LEAD,
        )
        strategy = SeniorityApprovalStrategy(config=config)
        evaluation = _make_evaluation(
            current_level=SeniorityLevel.MID,
            target_level=SeniorityLevel.SENIOR,
            direction=PromotionDirection.PROMOTION,
        )
        identity = make_agent_identity(level=SeniorityLevel.MID)

        decision = await strategy.decide(
            evaluation=evaluation,
            agent_identity=identity,
        )
        assert decision.auto_approve is True
        assert decision.requires_human is False


@pytest.mark.unit
class TestSeniorityApprovalDemotion:
    """Tests for demotion approval decisions."""

    async def test_demotion_auto_applies_cost_saving(
        self,
        approval_config: PromotionApprovalConfig,
    ) -> None:
        """Demotion auto-applies when auto_demote_cost_saving is True."""
        strategy = SeniorityApprovalStrategy(config=approval_config)
        evaluation = _make_evaluation(
            current_level=SeniorityLevel.MID,
            target_level=SeniorityLevel.JUNIOR,
            direction=PromotionDirection.DEMOTION,
        )
        identity = make_agent_identity(level=SeniorityLevel.MID)

        decision = await strategy.decide(
            evaluation=evaluation,
            agent_identity=identity,
        )
        assert decision.auto_approve is True
        assert decision.requires_human is False
        assert "cost-saving" in decision.reason

    async def test_demotion_authority_reducing_needs_human(self) -> None:
        """High-seniority demotion needs human when cost-saving is off."""
        config = PromotionApprovalConfig(
            auto_demote_cost_saving=False,
            human_demote_authority=True,
        )
        strategy = SeniorityApprovalStrategy(config=config)
        evaluation = _make_evaluation(
            current_level=SeniorityLevel.SENIOR,
            target_level=SeniorityLevel.MID,
            direction=PromotionDirection.DEMOTION,
        )
        identity = make_agent_identity(level=SeniorityLevel.SENIOR)

        decision = await strategy.decide(
            evaluation=evaluation,
            agent_identity=identity,
        )
        assert decision.auto_approve is False
        assert decision.requires_human is True
        assert "authority-reducing" in decision.reason

    async def test_demotion_below_senior_auto_applies_authority(self) -> None:
        """Below-Senior demotion auto-applies even with authority flag."""
        config = PromotionApprovalConfig(
            auto_demote_cost_saving=False,
            human_demote_authority=True,
        )
        strategy = SeniorityApprovalStrategy(config=config)
        evaluation = _make_evaluation(
            current_level=SeniorityLevel.MID,
            target_level=SeniorityLevel.JUNIOR,
            direction=PromotionDirection.DEMOTION,
        )
        identity = make_agent_identity(level=SeniorityLevel.MID)

        decision = await strategy.decide(
            evaluation=evaluation,
            agent_identity=identity,
        )
        assert decision.auto_approve is True
        assert decision.requires_human is False
