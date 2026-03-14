"""Milestone trust strategy.

Implements explicit capability milestones aligned with the Cloud
Security Alliance Agentic Trust Framework. Supports periodic
re-verification and trust decay on idle/error conditions.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from synthorg.core.enums import ToolAccessLevel  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.trust import (
    TRUST_DECAY_DETECTED,
    TRUST_EVALUATE_COMPLETE,
    TRUST_EVALUATE_START,
)
from synthorg.security.trust.levels import (
    TRANSITION_KEYS,
    TRUST_LEVEL_ORDER,
    TRUST_LEVEL_RANK,
)
from synthorg.security.trust.models import TrustEvaluationResult, TrustState

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from synthorg.core.types import NotBlankStr
    from synthorg.hr.performance.models import AgentPerformanceSnapshot
    from synthorg.security.trust.config import MilestoneCriteria, TrustConfig

logger = get_logger(__name__)

_RE_VERIFY_QUALITY_MIN: Final[float] = 7.0
_WINDOW_DAYS_SUFFIX: Final[str] = "d"


def _parse_window_days(window_size: str) -> int | None:
    """Extract day count from a window-size label like '7d'.

    Returns None if the label doesn't match the expected format.
    """
    if window_size.endswith(_WINDOW_DAYS_SUFFIX):
        try:
            return int(window_size[: -len(_WINDOW_DAYS_SUFFIX)])
        except ValueError:
            return None
    return None


class MilestoneTrustStrategy:
    """Trust strategy using explicit milestone gates.

    Each trust level transition has milestone criteria that must be
    met before the agent can advance. Trust can also decay if the
    agent is idle or error rates increase.
    """

    def __init__(self, *, config: TrustConfig) -> None:
        self._config = config
        self._milestones = config.milestones
        self._re_verification = config.re_verification

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        return "milestone"

    async def evaluate(
        self,
        *,
        agent_id: NotBlankStr,
        current_state: TrustState,
        snapshot: AgentPerformanceSnapshot,
    ) -> TrustEvaluationResult:
        """Evaluate milestones for potential trust promotion.

        Args:
            agent_id: Agent to evaluate.
            current_state: Current trust state.
            snapshot: Agent performance snapshot.

        Returns:
            Evaluation result with recommended level.
        """
        logger.debug(
            TRUST_EVALUATE_START,
            agent_id=agent_id,
            strategy="milestone",
        )

        now = datetime.now(UTC)
        recommended = current_state.global_level
        requires_human = False
        details_parts: list[str] = []

        # Check for promotion through milestones
        for key, from_level, to_level in TRANSITION_KEYS:
            if current_state.global_level != from_level:
                continue

            milestone = self._milestones.get(key)
            if milestone is None:
                continue

            if self._check_milestone(
                milestone=milestone,
                snapshot=snapshot,
                state=current_state,
                now=now,
            ):
                recommended = to_level
                requires_human = milestone.requires_human_approval
                details_parts.append(f"Milestone {key!r} achieved")
                break

        # Check for decay (only if at RESTRICTED or above)
        if (
            self._re_verification.enabled
            and TRUST_LEVEL_RANK.get(current_state.global_level, 0) > 0
        ):
            decay_level = self._check_decay(
                state=current_state,
                snapshot=snapshot,
                now=now,
            )
            if decay_level is not None:
                recommended = decay_level
                requires_human = False
                details_parts.append(f"Trust decayed to {decay_level.value}")

        if not details_parts:
            details_parts.append("No milestone changes")

        result = TrustEvaluationResult(
            agent_id=agent_id,
            recommended_level=recommended,
            current_level=current_state.global_level,
            requires_human_approval=requires_human,
            details="; ".join(details_parts),
            strategy_name="milestone",
        )

        logger.debug(
            TRUST_EVALUATE_COMPLETE,
            agent_id=agent_id,
            recommended=recommended.value,
        )
        return result

    def initial_state(self, *, agent_id: NotBlankStr) -> TrustState:
        """Create initial trust state at the configured level.

        Args:
            agent_id: Agent identifier.

        Returns:
            Initial trust state with milestone progress.
        """
        return TrustState(
            agent_id=agent_id,
            global_level=self._config.initial_level,
            milestone_progress={},
        )

    def _check_milestone(
        self,
        *,
        milestone: MilestoneCriteria,
        snapshot: AgentPerformanceSnapshot,
        state: TrustState,
        now: AwareDatetime,
    ) -> bool:
        """Check whether all milestone criteria are met."""
        if not self._check_tasks_and_quality(milestone, snapshot):
            return False

        return self._check_time_and_history(
            milestone,
            snapshot,
            state,
            now,
        )

    @staticmethod
    def _check_tasks_and_quality(
        milestone: MilestoneCriteria,
        snapshot: AgentPerformanceSnapshot,
    ) -> bool:
        """Check task count and quality criteria."""
        # Best single-window task count (not cumulative total)
        max_tasks_completed = 0
        for window in snapshot.windows:
            max_tasks_completed = max(max_tasks_completed, window.tasks_completed)

        if max_tasks_completed < milestone.tasks_completed:
            return False

        quality = snapshot.overall_quality_score
        if quality is not None and quality < milestone.quality_score_min:
            return False

        return not (quality is None and milestone.quality_score_min > 0.0)

    @staticmethod
    def _check_time_and_history(
        milestone: MilestoneCriteria,
        snapshot: AgentPerformanceSnapshot,
        state: TrustState,
        now: AwareDatetime,
    ) -> bool:
        """Check time active and clean history criteria."""
        if milestone.time_active_days > 0:
            # Use created_at (agent tenure) rather than last_evaluated_at
            if state.created_at is None:
                return False
            days_active = (now - state.created_at).days
            if days_active < milestone.time_active_days:
                return False

        if milestone.clean_history_days > 0:
            for window in snapshot.windows:
                # Only check windows whose period fits within the
                # required clean-history duration (e.g. skip the 90d
                # window when clean_history_days=7).
                window_days = _parse_window_days(str(window.window_size))
                if (
                    window_days is not None
                    and window_days > milestone.clean_history_days
                ):
                    continue
                if (
                    window.data_point_count > 0
                    and window.success_rate is not None
                    and window.success_rate < 1.0
                ):
                    return False

        return True

    def _check_decay(
        self,
        *,
        state: TrustState,
        snapshot: AgentPerformanceSnapshot,
        now: AwareDatetime,
    ) -> ToolAccessLevel | None:
        """Check for trust decay conditions.

        Returns the demoted level if decay should occur, else None.
        """
        current_rank = TRUST_LEVEL_RANK.get(state.global_level, 0)
        if current_rank <= 0:
            return None

        # Idle decay
        last_eval = state.last_evaluated_at
        if last_eval is not None:
            idle_days = (now - last_eval).days
            if idle_days >= self._re_verification.decay_on_idle_days:
                demoted = TRUST_LEVEL_ORDER[current_rank - 1]
                logger.info(
                    TRUST_DECAY_DETECTED,
                    agent_id=state.agent_id,
                    reason="idle",
                    idle_days=idle_days,
                    threshold=self._re_verification.decay_on_idle_days,
                )
                return demoted

        # Error rate decay
        for window in snapshot.windows:
            if window.data_point_count > 0 and window.success_rate is not None:
                error_rate = 1.0 - window.success_rate
                if error_rate > self._re_verification.decay_on_error_rate:
                    demoted = TRUST_LEVEL_ORDER[current_rank - 1]
                    logger.info(
                        TRUST_DECAY_DETECTED,
                        agent_id=state.agent_id,
                        reason="error_rate",
                        error_rate=error_rate,
                        threshold=self._re_verification.decay_on_error_rate,
                    )
                    return demoted
                break

        # Re-verification interval
        if (
            state.last_decay_check_at is not None
            and (now - state.last_decay_check_at)
            >= timedelta(days=self._re_verification.interval_days)
            and snapshot.overall_quality_score is not None
            and snapshot.overall_quality_score < _RE_VERIFY_QUALITY_MIN
        ):
            demoted = TRUST_LEVEL_ORDER[current_rank - 1]
            logger.info(
                TRUST_DECAY_DETECTED,
                agent_id=state.agent_id,
                reason="re_verification_failed",
            )
            return demoted

        return None
