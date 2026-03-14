"""Threshold-based promotion criteria evaluator.

Implements configurable N-of-M threshold gates for promotion/demotion
criteria evaluation (DESIGN_SPEC D13).
"""

from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.core.enums import SeniorityLevel, compare_seniority
from synthorg.hr.enums import PromotionDirection
from synthorg.hr.promotion.models import CriterionResult, PromotionEvaluation
from synthorg.observability import get_logger
from synthorg.observability.events.promotion import (
    PROMOTION_EVALUATE_COMPLETE,
    PROMOTION_EVALUATE_START,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.types import NotBlankStr
    from synthorg.hr.performance.models import AgentPerformanceSnapshot
    from synthorg.hr.promotion.config import PromotionCriteriaConfig

logger = get_logger(__name__)

# Default thresholds per criterion, keyed by criterion name.
_DEFAULT_THRESHOLDS: MappingProxyType[str, float] = MappingProxyType(
    {
        "quality_score": 7.0,
        "success_rate": 0.8,
        "tasks_completed": 10.0,
    }
)

_DEMOTION_THRESHOLDS: MappingProxyType[str, float] = MappingProxyType(
    {
        "quality_score": 4.0,
        "success_rate": 0.5,
        "tasks_completed": 3.0,
    }
)


class ThresholdEvaluator:
    """Configurable threshold gate evaluator for promotion criteria.

    Uses N-of-M logic: agent must meet at least ``min_criteria_met``
    of the total criteria, plus all ``required_criteria``.
    """

    def __init__(self, *, config: PromotionCriteriaConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        return "threshold_evaluator"

    async def evaluate(
        self,
        *,
        agent_id: NotBlankStr,
        current_level: SeniorityLevel,
        target_level: SeniorityLevel,
        snapshot: AgentPerformanceSnapshot,
    ) -> PromotionEvaluation:
        """Evaluate criteria for a level change.

        Args:
            agent_id: Agent to evaluate.
            current_level: Current seniority level.
            target_level: Target seniority level.
            snapshot: Agent performance snapshot.

        Returns:
            Evaluation result with criteria details.
        """
        direction = (
            PromotionDirection.PROMOTION
            if compare_seniority(target_level, current_level) > 0
            else PromotionDirection.DEMOTION
        )

        logger.debug(
            PROMOTION_EVALUATE_START,
            agent_id=agent_id,
            current_level=current_level.value,
            target_level=target_level.value,
            direction=direction.value,
        )

        thresholds = (
            _DEFAULT_THRESHOLDS
            if direction == PromotionDirection.PROMOTION
            else _DEMOTION_THRESHOLDS
        )

        criteria_results = self._evaluate_criteria(
            snapshot=snapshot,
            thresholds=thresholds,
            direction=direction,
        )

        met_count = sum(1 for c in criteria_results if c.met)
        required_names = set(self._config.required_criteria)

        required_met = all(c.met for c in criteria_results if c.name in required_names)

        eligible = met_count >= self._config.min_criteria_met and required_met

        now = datetime.now(UTC)
        evaluation = PromotionEvaluation(
            agent_id=agent_id,
            current_level=current_level,
            target_level=target_level,
            direction=direction,
            criteria_results=tuple(criteria_results),
            required_criteria_met=required_met,
            eligible=eligible,
            evaluated_at=now,
            strategy_name="threshold_evaluator",
        )

        logger.debug(
            PROMOTION_EVALUATE_COMPLETE,
            agent_id=agent_id,
            eligible=eligible,
            met_count=met_count,
        )
        return evaluation

    @staticmethod
    def _evaluate_criteria(
        *,
        snapshot: AgentPerformanceSnapshot,
        thresholds: Mapping[str, float],
        direction: PromotionDirection,
    ) -> list[CriterionResult]:
        """Evaluate individual criteria against thresholds."""
        results: list[CriterionResult] = []

        # Quality score criterion
        quality = snapshot.overall_quality_score or 0.0
        quality_threshold = thresholds.get("quality_score", 7.0)
        if direction == PromotionDirection.PROMOTION:
            quality_met = quality >= quality_threshold
        else:
            quality_met = quality < quality_threshold
        results.append(
            CriterionResult(
                name="quality_score",
                met=quality_met,
                current_value=quality,
                threshold=quality_threshold,
            )
        )

        # Success rate criterion — use most recent window value
        success_rate = 0.0
        for window in snapshot.windows:
            if window.success_rate is not None:
                success_rate = window.success_rate
                break
        rate_threshold = thresholds.get("success_rate", 0.8)
        if direction == PromotionDirection.PROMOTION:
            rate_met = success_rate >= rate_threshold
        else:
            rate_met = success_rate < rate_threshold
        results.append(
            CriterionResult(
                name="success_rate",
                met=rate_met,
                current_value=success_rate,
                threshold=rate_threshold,
            )
        )

        # Tasks completed criterion — best single-window count
        max_tasks_completed = 0.0
        for window in snapshot.windows:
            max_tasks_completed = max(
                max_tasks_completed,
                float(window.tasks_completed),
            )
        tasks_threshold = thresholds.get("tasks_completed", 10.0)
        if direction == PromotionDirection.PROMOTION:
            tasks_met = max_tasks_completed >= tasks_threshold
        else:
            tasks_met = max_tasks_completed < tasks_threshold
        results.append(
            CriterionResult(
                name="tasks_completed",
                met=tasks_met,
                current_value=max_tasks_completed,
                threshold=tasks_threshold,
            )
        )

        return results
