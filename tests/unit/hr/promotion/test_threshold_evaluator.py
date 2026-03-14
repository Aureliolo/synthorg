"""Unit tests for ThresholdEvaluator promotion criteria strategy."""

from typing import TYPE_CHECKING

import pytest

from synthorg.core.enums import SeniorityLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import PromotionDirection
from synthorg.hr.promotion.threshold_evaluator import ThresholdEvaluator

from .conftest import make_performance_snapshot

if TYPE_CHECKING:
    from synthorg.hr.promotion.config import PromotionCriteriaConfig

pytestmark = [pytest.mark.unit, pytest.mark.timeout(30)]


@pytest.mark.unit
class TestThresholdEvaluatorName:
    """Tests for ThresholdEvaluator identity."""

    def test_name(self, criteria_config: PromotionCriteriaConfig) -> None:
        """Strategy name is 'threshold_evaluator'."""
        evaluator = ThresholdEvaluator(config=criteria_config)
        assert evaluator.name == "threshold_evaluator"


@pytest.mark.unit
class TestThresholdEvaluatorPromotion:
    """Tests for promotion evaluation via ThresholdEvaluator."""

    async def test_good_metrics_eligible(
        self,
        criteria_config: PromotionCriteriaConfig,
    ) -> None:
        """Agent with good metrics is eligible for promotion."""
        evaluator = ThresholdEvaluator(config=criteria_config)
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=8.0,
            success_rate=0.9,
            tasks_completed=15,
        )
        result = await evaluator.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            snapshot=snapshot,
        )
        assert result.eligible is True
        assert result.direction == PromotionDirection.PROMOTION
        assert result.criteria_met_count >= 2
        assert result.strategy_name == "threshold_evaluator"

    async def test_poor_metrics_not_eligible(
        self,
        criteria_config: PromotionCriteriaConfig,
    ) -> None:
        """Agent with poor metrics is not eligible for promotion."""
        evaluator = ThresholdEvaluator(config=criteria_config)
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=3.0,
            success_rate=0.4,
            tasks_completed=3,
        )
        result = await evaluator.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            snapshot=snapshot,
        )
        assert result.eligible is False
        assert result.criteria_met_count == 0

    @pytest.mark.parametrize(
        ("quality", "success_rate", "tasks_completed", "expected_eligible"),
        [
            pytest.param(7.0, 0.8, 10, True, id="at-threshold-all-met"),
            pytest.param(6.9, 0.8, 10, True, id="quality-below-2-of-3-met"),
            pytest.param(7.0, 0.79, 10, True, id="success-rate-below-2-of-3-met"),
            pytest.param(7.0, 0.8, 9, True, id="tasks-below-2-of-3-met"),
            pytest.param(6.0, 0.7, 10, False, id="two-below-only-1-met"),
            pytest.param(5.0, 0.5, 5, False, id="all-below-threshold"),
            pytest.param(5.0, 0.9, 15, True, id="low-quality-but-2-met"),
        ],
    )
    async def test_boundary_cases(
        self,
        quality: float,
        success_rate: float,
        tasks_completed: int,
        expected_eligible: bool,
        criteria_config: PromotionCriteriaConfig,
    ) -> None:
        """Parametrized boundary conditions for promotion eligibility."""
        evaluator = ThresholdEvaluator(config=criteria_config)
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=quality,
            success_rate=success_rate,
            tasks_completed=tasks_completed,
        )
        result = await evaluator.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_level=SeniorityLevel.JUNIOR,
            target_level=SeniorityLevel.MID,
            snapshot=snapshot,
        )
        assert result.eligible is expected_eligible


@pytest.mark.unit
class TestThresholdEvaluatorDemotion:
    """Tests for demotion evaluation via ThresholdEvaluator."""

    async def test_demotion_direction(
        self,
        criteria_config: PromotionCriteriaConfig,
    ) -> None:
        """Evaluating a lower target level produces DEMOTION direction."""
        evaluator = ThresholdEvaluator(config=criteria_config)
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=3.0,
            success_rate=0.3,
            tasks_completed=0,
        )
        result = await evaluator.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_level=SeniorityLevel.MID,
            target_level=SeniorityLevel.JUNIOR,
            snapshot=snapshot,
        )
        assert result.direction == PromotionDirection.DEMOTION
        assert result.current_level == SeniorityLevel.MID
        assert result.target_level == SeniorityLevel.JUNIOR

    async def test_demotion_poor_metrics_eligible(
        self,
        criteria_config: PromotionCriteriaConfig,
    ) -> None:
        """Agent with poor metrics is eligible for demotion.

        Demotion thresholds check whether the value is *below*
        the threshold (quality < 4.0, success_rate < 0.5, etc.).
        """
        evaluator = ThresholdEvaluator(config=criteria_config)
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=2.0,
            success_rate=0.3,
            tasks_completed=0,
        )
        result = await evaluator.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_level=SeniorityLevel.MID,
            target_level=SeniorityLevel.JUNIOR,
            snapshot=snapshot,
        )
        assert result.eligible is True
        assert result.criteria_met_count >= 2

    async def test_demotion_good_metrics_not_eligible(
        self,
        criteria_config: PromotionCriteriaConfig,
    ) -> None:
        """Agent with good metrics should NOT be eligible for demotion."""
        evaluator = ThresholdEvaluator(config=criteria_config)
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=8.0,
            success_rate=0.9,
            tasks_completed=15,
        )
        result = await evaluator.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_level=SeniorityLevel.MID,
            target_level=SeniorityLevel.JUNIOR,
            snapshot=snapshot,
        )
        assert result.eligible is False
