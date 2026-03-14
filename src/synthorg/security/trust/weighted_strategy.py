"""Weighted trust strategy.

Computes a single trust score from weighted performance factors
and promotes/demotes based on configurable thresholds.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import ToolAccessLevel  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.trust import (
    TRUST_EVALUATE_COMPLETE,
    TRUST_EVALUATE_START,
)
from synthorg.security.trust.levels import TRANSITION_KEYS
from synthorg.security.trust.models import TrustEvaluationResult, TrustState

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.performance.models import AgentPerformanceSnapshot
    from synthorg.security.trust.config import TrustConfig, TrustThreshold

logger = get_logger(__name__)


class WeightedTrustStrategy:
    """Trust strategy using a single weighted score.

    Computes a trust score from four weighted factors derived from
    the agent's performance snapshot:
      - task_difficulty: quality score normalized to [0, 1]
      - completion_rate: task success rate from the latest window
      - error_rate: failure penalty (tasks_failed / data_point_count)
      - human_feedback: task volume proxy (tasks / 100, capped at 1.0)
        — placeholder until actual human feedback signals are available

    The score is compared against configurable thresholds to
    determine the recommended trust level.  Trust changes are
    restricted to one adjacent level per evaluation.
    """

    def __init__(self, *, config: TrustConfig) -> None:
        self._config = config
        self._weights = config.weights
        self._thresholds = config.promotion_thresholds

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        return "weighted"

    async def evaluate(
        self,
        *,
        agent_id: NotBlankStr,
        current_state: TrustState,
        snapshot: AgentPerformanceSnapshot,
    ) -> TrustEvaluationResult:
        """Compute weighted trust score and recommend level.

        Args:
            agent_id: Agent to evaluate.
            current_state: Current trust state.
            snapshot: Agent performance snapshot.

        Returns:
            Evaluation result with score and recommended level.
        """
        logger.debug(
            TRUST_EVALUATE_START,
            agent_id=agent_id,
            strategy="weighted",
        )

        score = self._compute_score(snapshot)
        recommended = self._score_to_level(score, current_state.global_level)
        requires_human = self._check_human_approval(
            current_state.global_level,
            recommended,
        )

        result = TrustEvaluationResult(
            agent_id=agent_id,
            recommended_level=recommended,
            current_level=current_state.global_level,
            requires_human_approval=requires_human,
            score=score,
            details=(f"Weighted score {score:.4f}; recommended {recommended.value}"),
            strategy_name="weighted",
        )

        logger.debug(
            TRUST_EVALUATE_COMPLETE,
            agent_id=agent_id,
            score=score,
            recommended=recommended.value,
        )
        return result

    def initial_state(self, *, agent_id: NotBlankStr) -> TrustState:
        """Create initial trust state at the configured level.

        Args:
            agent_id: Agent identifier.

        Returns:
            Initial trust state.
        """
        return TrustState(
            agent_id=agent_id,
            global_level=self._config.initial_level,
            trust_score=0.0,
        )

    def _compute_score(self, snapshot: AgentPerformanceSnapshot) -> float:
        """Compute the weighted trust score from performance data.

        Each factor uses a distinct data source:
        - difficulty: quality score (normalized 0-1)
        - completion: success rate from latest window
        - error: 1 - (tasks_failed / data_point_count), distinct from
          success_rate because data_point_count includes non-task events
        - feedback: task volume ratio (tasks/100, capped at 1.0)
          — placeholder for human feedback signals
        """
        # Quality score normalized to [0, 1]
        difficulty_factor = (
            snapshot.overall_quality_score / 10.0
            if snapshot.overall_quality_score is not None
            else 0.0
        )

        # Success rate from latest window
        completion_factor = 0.0
        for window in snapshot.windows:
            if window.success_rate is not None:
                completion_factor = window.success_rate
                break

        # Error penalty: 1 - (tasks_failed / data_point_count)
        error_factor = 1.0
        for window in snapshot.windows:
            if window.data_point_count > 0:
                error_factor = 1.0 - (window.tasks_failed / window.data_point_count)
                break

        # Task volume ratio (tasks completed / 100, capped at 1.0)
        # — placeholder for human feedback until that signal is available
        feedback_factor = 0.0
        for window in snapshot.windows:
            if window.tasks_completed > 0:
                feedback_factor = min(window.tasks_completed / 100.0, 1.0)
                break

        score = (
            self._weights.task_difficulty * difficulty_factor
            + self._weights.completion_rate * completion_factor
            + self._weights.error_rate * error_factor
            + self._weights.human_feedback * feedback_factor
        )
        return round(min(max(score, 0.0), 1.0), 4)

    def _score_to_level(
        self,
        score: float,
        current_level: ToolAccessLevel,
    ) -> ToolAccessLevel:
        """Determine the next adjacent trust level from score.

        Only considers the immediate next transition from the current
        level — trust changes are restricted to one level per evaluation.
        """
        for key, from_level, to_level in TRANSITION_KEYS:
            if from_level != current_level:
                continue

            threshold = self._thresholds.get(key)
            if threshold is None:
                continue

            if score >= threshold.score:
                return to_level

        return current_level

    def _check_human_approval(
        self,
        current: ToolAccessLevel,
        recommended: ToolAccessLevel,
    ) -> bool:
        """Check if the transition requires human approval."""
        if current == recommended:
            return False

        for key, from_level, to_level in TRANSITION_KEYS:
            if from_level == current and to_level == recommended:
                threshold: TrustThreshold | None = self._thresholds.get(key)
                if threshold is not None:
                    return threshold.requires_human_approval
        return False
