"""CI signal quality scoring strategy (D2 Layer 1).

Scores task quality based on acceptance criteria met ratio,
task success, and cost efficiency. Pure computation, no I/O.
"""

import math
from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import QualityScoreResult, TaskMetricRecord
from synthorg.observability import get_logger
from synthorg.observability.events.performance import PERF_QUALITY_SCORED

if TYPE_CHECKING:
    from synthorg.core.task import AcceptanceCriterion

logger = get_logger(__name__)

# Scoring weights.
_CRITERIA_WEIGHT: float = 0.70
_SUCCESS_WEIGHT: float = 0.20
_COST_EFFICIENCY_WEIGHT: float = 0.10

# Maximum score.
_MAX_SCORE: float = 10.0


class CISignalQualityStrategy:
    """Quality scoring based on CI signals (acceptance criteria, success, cost).

    Scoring breakdown:
        - Acceptance criteria met ratio: 70% weight.
        - Task success: 20% weight (10.0 if success, 0.0 if failure).
        - Cost efficiency vs budget: 10% weight (log-scaled, configurable).

    When no acceptance criteria are provided, the criteria component
    scores 10.0 (all criteria trivially met) with lower confidence.

    Args:
        cost_budget: Reference budget for cost efficiency scoring.
            Tasks at or below this cost get full marks; tasks above
            are penalized on a log scale. Defaults to 100.0 USD.
    """

    def __init__(
        self,
        *,
        cost_budget: float = 100.0,
    ) -> None:
        self._cost_budget = max(cost_budget, 0.01)

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "ci_signal"

    async def score(
        self,
        *,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
        task_result: TaskMetricRecord,
        acceptance_criteria: tuple[AcceptanceCriterion, ...],
    ) -> QualityScoreResult:
        """Score task completion quality from CI signals.

        Args:
            agent_id: Agent who completed the task.
            task_id: Task identifier.
            task_result: Recorded task metrics.
            acceptance_criteria: Criteria to evaluate against.

        Returns:
            Quality score result with breakdown and confidence.
        """
        # Criteria met ratio.
        if acceptance_criteria:
            met_count = sum(1 for c in acceptance_criteria if c.met)
            criteria_ratio = met_count / len(acceptance_criteria)
            criteria_confidence = 1.0
        else:
            criteria_ratio = 1.0
            criteria_confidence = 0.5

        criteria_score = criteria_ratio * _MAX_SCORE

        # Task success bonus.
        success_score = _MAX_SCORE if task_result.is_success else 0.0

        # Cost efficiency: log-scaled relative to budget.
        # Tasks at or below budget get full marks; above budget the
        # score decays logarithmically so high-cost tasks still
        # differentiate instead of all collapsing to 0.
        ratio = task_result.cost_usd / self._cost_budget
        if ratio <= 1.0:
            cost_score = _MAX_SCORE
        else:
            cost_score = max(0.0, _MAX_SCORE * (1.0 - math.log10(ratio)))

        # Weighted total.
        total = (
            criteria_score * _CRITERIA_WEIGHT
            + success_score * _SUCCESS_WEIGHT
            + cost_score * _COST_EFFICIENCY_WEIGHT
        )
        total = max(0.0, min(_MAX_SCORE, total))

        # Confidence based on data availability.
        confidence = criteria_confidence * (0.8 if task_result.is_success else 0.6)

        breakdown = (
            ("acceptance_criteria", round(criteria_score, 4)),
            ("task_success", round(success_score, 4)),
            ("cost_efficiency", round(cost_score, 4)),
        )

        result = QualityScoreResult(
            score=round(total, 4),
            strategy_name=NotBlankStr(self.name),
            breakdown=breakdown,
            confidence=round(confidence, 4),
        )

        logger.debug(
            PERF_QUALITY_SCORED,
            agent_id=agent_id,
            task_id=task_id,
            score=result.score,
            strategy=self.name,
        )
        return result
