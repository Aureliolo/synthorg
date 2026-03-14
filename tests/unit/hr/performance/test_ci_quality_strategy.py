"""Tests for CISignalQualityStrategy."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.ci_quality_strategy import CISignalQualityStrategy

from .conftest import make_acceptance_criterion, make_task_metric


@pytest.mark.unit
class TestCISignalQualityStrategy:
    """CISignalQualityStrategy scoring logic."""

    def _make_strategy(self) -> CISignalQualityStrategy:
        return CISignalQualityStrategy()

    async def test_name(self) -> None:
        assert self._make_strategy().name == "ci_signal"

    async def test_all_criteria_met_success(self) -> None:
        """All criteria met + success -> high score."""
        strategy = self._make_strategy()
        criteria = (
            make_acceptance_criterion(description="Tests pass", met=True),
            make_acceptance_criterion(description="Lint clean", met=True),
        )
        task_result = make_task_metric(is_success=True, cost_usd=0.0)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=task_result,
            acceptance_criteria=criteria,
        )

        assert result.score == 10.0
        assert result.strategy_name == "ci_signal"
        assert result.confidence == 0.8  # 1.0 * 0.8 (success path)
        assert len(result.breakdown) == 3

    async def test_no_criteria_met_failure(self) -> None:
        """No criteria met + failure + cost exceeding budget -> zero score."""
        strategy = self._make_strategy()
        criteria = (
            make_acceptance_criterion(description="Tests pass", met=False),
            make_acceptance_criterion(description="Lint clean", met=False),
        )
        task_result = make_task_metric(is_success=False, cost_usd=1000.0)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=task_result,
            acceptance_criteria=criteria,
        )

        assert result.score == 0.0
        assert result.confidence == 0.6  # 1.0 * 0.6 (failure path)

    async def test_partial_criteria(self) -> None:
        """Half criteria met -> proportional criteria component."""
        strategy = self._make_strategy()
        criteria = (
            make_acceptance_criterion(description="Tests pass", met=True),
            make_acceptance_criterion(description="Lint clean", met=False),
        )
        task_result = make_task_metric(is_success=True, cost_usd=0.0)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=task_result,
            acceptance_criteria=criteria,
        )

        # criteria: (0.5 * 10) * 0.70 = 3.5
        # success: 10.0 * 0.20 = 2.0
        # cost: 10.0 * 0.10 = 1.0
        # total = 6.5
        assert result.score == 6.5

    async def test_empty_criteria_high_score_low_confidence(self) -> None:
        """Empty criteria -> criteria=10.0 but confidence halved."""
        strategy = self._make_strategy()
        task_result = make_task_metric(is_success=True, cost_usd=0.0)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=task_result,
            acceptance_criteria=(),
        )

        assert result.score == 10.0
        # Confidence: 0.5 * 0.8 = 0.4
        assert result.confidence == 0.4

    async def test_success_bonus(self) -> None:
        """Success=True gives 10.0 success component."""
        strategy = self._make_strategy()
        task_result = make_task_metric(is_success=True, cost_usd=5.0)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=task_result,
            acceptance_criteria=(),
        )

        breakdown_dict = dict(result.breakdown)
        assert breakdown_dict["task_success"] == 10.0

    async def test_failure_no_bonus(self) -> None:
        """Success=False gives 0.0 success component."""
        strategy = self._make_strategy()
        task_result = make_task_metric(is_success=False, cost_usd=5.0)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=task_result,
            acceptance_criteria=(),
        )

        breakdown_dict = dict(result.breakdown)
        assert breakdown_dict["task_success"] == 0.0

    async def test_cost_efficiency_zero_cost(self) -> None:
        """Zero cost -> max cost efficiency score."""
        strategy = self._make_strategy()
        task_result = make_task_metric(cost_usd=0.0)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=task_result,
            acceptance_criteria=(),
        )

        breakdown_dict = dict(result.breakdown)
        assert breakdown_dict["cost_efficiency"] == 10.0

    async def test_cost_efficiency_high_cost(self) -> None:
        """Cost exceeding budget by 10x -> zero cost efficiency score."""
        strategy = self._make_strategy()
        task_result = make_task_metric(cost_usd=1000.0)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=task_result,
            acceptance_criteria=(),
        )

        breakdown_dict = dict(result.breakdown)
        assert breakdown_dict["cost_efficiency"] == 0.0

    @pytest.mark.parametrize(
        ("cost_usd", "expected_cost_score"),
        [
            (0.0, 10.0),
            (5.0, 10.0),
            (100.0, 10.0),
            (1000.0, 0.0),
        ],
        ids=["zero", "within_budget", "at_budget", "10x_over"],
    )
    async def test_cost_efficiency_parametrized(
        self,
        cost_usd: float,
        expected_cost_score: float,
    ) -> None:
        strategy = self._make_strategy()
        task_result = make_task_metric(cost_usd=cost_usd, is_success=True)

        result = await strategy.score(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=task_result,
            acceptance_criteria=(),
        )

        breakdown_dict = dict(result.breakdown)
        assert breakdown_dict["cost_efficiency"] == expected_cost_score
