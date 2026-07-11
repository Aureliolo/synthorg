"""CI signal quality scoring strategy (D2 Layer 1).

Scores task quality based on acceptance criteria met ratio,
task success, and cost efficiency. Pure computation, no I/O.
"""

import math
from typing import Final

from synthorg.core.task import AcceptanceCriterion
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import QualityScoreResult, TaskMetricRecord
from synthorg.observability import get_logger
from synthorg.observability.events.performance import PERF_QUALITY_SCORED

logger = get_logger(__name__)
_DEFAULT_COST_BUDGET: Final[float] = 100.0

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
            are penalized on a log scale. Defaults to 100.0.
    """

    def __init__(
        self,
        *,
        cost_budget: float = _DEFAULT_COST_BUDGET,
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
        # No acceptance criteria -> trivially met at full score, lower confidence.
        if acceptance_criteria:
            met = sum(1 for c in acceptance_criteria if c.met)
            criteria_score = (met / len(acceptance_criteria)) * _MAX_SCORE
            criteria_confidence = 1.0
        else:
            criteria_score = _MAX_SCORE
            criteria_confidence = 0.5
        success_score = _MAX_SCORE if task_result.is_success else 0.0
        # Cost efficiency: full marks at/below budget, then log-decays; an
        # unmeasured cost drops the component (renormalising the weights below).
        cost_score: float | None = None
        if task_result.cost is not None:
            ratio = task_result.cost / self._cost_budget
            cost_score = (
                _MAX_SCORE
                if ratio <= 1.0
                else max(0.0, _MAX_SCORE * (1.0 - math.log10(ratio)))
            )
        weighted = criteria_score * _CRITERIA_WEIGHT + success_score * _SUCCESS_WEIGHT
        weight_sum = _CRITERIA_WEIGHT + _SUCCESS_WEIGHT
        if cost_score is not None:
            weighted += cost_score * _COST_EFFICIENCY_WEIGHT
            weight_sum += _COST_EFFICIENCY_WEIGHT
        total = max(0.0, min(_MAX_SCORE, weighted / weight_sum))
        confidence = criteria_confidence * (0.8 if task_result.is_success else 0.6)
        # Omit cost_efficiency when unmeasured: a 0.0 would read as "scored zero
        # efficiency" rather than "not scored".
        breakdown: list[tuple[str, float]] = [
            ("acceptance_criteria", round(criteria_score, 4)),
            ("task_success", round(success_score, 4)),
        ]
        if cost_score is not None:
            breakdown.append(("cost_efficiency", round(cost_score, 4)))

        logger.debug(
            PERF_QUALITY_SCORED,
            agent_id=agent_id,
            task_id=task_id,
            score=round(total, 4),
            strategy=self.name,
        )
        return QualityScoreResult(
            score=round(total, 4),
            strategy_name=NotBlankStr(self.name),
            breakdown=tuple(breakdown),
            confidence=round(confidence, 4),
        )
