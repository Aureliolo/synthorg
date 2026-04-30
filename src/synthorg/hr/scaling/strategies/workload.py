"""Workload auto-scale strategy.

Proposes hiring when utilization exceeds a threshold for sustained
windows, and pruning when utilization drops below a floor.
"""

from datetime import UTC, datetime

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.enums import ScalingActionType, ScalingStrategyName
from synthorg.hr.scaling.models import ScalingContext, ScalingDecision
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_SCALING_STRATEGY_EVALUATED

logger = get_logger(__name__)

_NAME = NotBlankStr("workload")
_ACTION_TYPES = frozenset({ScalingActionType.HIRE, ScalingActionType.PRUNE})


class WorkloadAutoScaleStrategy:
    """Rule-based workload scaling.

    Evaluates average utilization from workload signals and proposes
    hiring (above threshold) or pruning (below floor).

    Args:
        hire_threshold: Utilization fraction above which to hire.
            Default 0.85 -- hire when sustained utilization exceeds
            85% so the team is not at risk of saturation under
            transient load spikes.
        prune_threshold: Utilization fraction below which to prune.
            Default 0.30 -- prune when sustained utilization drops
            below 30% so headroom is reclaimed without churning the
            team on temporary lulls.
    """

    def __init__(
        self,
        *,
        hire_threshold: float = 0.85,
        prune_threshold: float = 0.30,
    ) -> None:
        if not 0.0 <= prune_threshold < hire_threshold <= 1.0:
            msg = (
                f"thresholds must satisfy 0.0 <= prune_threshold ({prune_threshold}) "
                f"< hire_threshold ({hire_threshold}) <= 1.0"
            )
            raise ValueError(msg)
        self._hire_threshold = hire_threshold
        self._prune_threshold = prune_threshold

    @property
    def name(self) -> NotBlankStr:
        """Strategy identifier."""
        return _NAME

    @property
    def action_types(self) -> frozenset[ScalingActionType]:
        """Action types this strategy can produce."""
        return _ACTION_TYPES

    async def evaluate(
        self,
        context: ScalingContext,
    ) -> tuple[ScalingDecision, ...]:
        """Evaluate workload signals and propose scaling decisions.

        Args:
            context: Aggregated company state snapshot.

        Returns:
            Hire or prune decisions based on utilization.
        """
        now = datetime.now(UTC)
        decisions: list[ScalingDecision] = []

        # Find avg_utilization signal.
        avg_util = next(
            (s for s in context.workload_signals if s.name == "avg_utilization"),
            None,
        )
        if avg_util is None:
            logger.info(
                HR_SCALING_STRATEGY_EVALUATED,
                strategy="workload",
                decisions=0,
                reason="no_utilization_signal",
            )
            return ()

        workload_signals = (avg_util,)

        if avg_util.value > self._hire_threshold:
            confidence = min(
                (avg_util.value - self._hire_threshold)
                / (1.0 - self._hire_threshold + 1e-9),
                1.0,
            )
            decisions.append(
                ScalingDecision(
                    action_type=ScalingActionType.HIRE,
                    source_strategy=ScalingStrategyName.WORKLOAD,
                    target_role=NotBlankStr("general"),
                    rationale=NotBlankStr(
                        f"avg utilization {avg_util.value:.0%} exceeds "
                        f"threshold {self._hire_threshold:.0%}"
                    ),
                    confidence=round(confidence, 4),
                    signals=workload_signals,
                    created_at=now,
                ),
            )

        elif avg_util.value < self._prune_threshold and context.active_agent_count > 1:
            # Placeholder target -- per-agent utilization-based selection
            # is a future enhancement.
            target = context.agent_ids[-1] if context.agent_ids else None
            if target is not None:
                confidence = min(
                    (self._prune_threshold - avg_util.value)
                    / (self._prune_threshold + 1e-9),
                    1.0,
                )
                decisions.append(
                    ScalingDecision(
                        action_type=ScalingActionType.PRUNE,
                        source_strategy=ScalingStrategyName.WORKLOAD,
                        target_agent_id=target,
                        rationale=NotBlankStr(
                            f"avg utilization {avg_util.value:.0%} below "
                            f"threshold {self._prune_threshold:.0%}"
                        ),
                        confidence=round(confidence, 4),
                        signals=workload_signals,
                        created_at=now,
                    ),
                )

        logger.info(
            HR_SCALING_STRATEGY_EVALUATED,
            strategy="workload",
            decisions=len(decisions),
            avg_utilization=avg_util.value,
        )
        return tuple(decisions)
