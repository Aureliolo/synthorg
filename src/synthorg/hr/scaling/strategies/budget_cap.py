"""Budget cap strategy.

Hard ceiling on spend: proposes pruning when projected burn
exceeds the safety margin, and emits HOLD to block hires
from lower-priority strategies.
"""

from datetime import UTC, datetime
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.enums import ScalingActionType, ScalingStrategyName
from synthorg.hr.scaling.models import ScalingContext, ScalingDecision
from synthorg.observability import get_logger
from synthorg.observability.events.hr import (
    HR_SCALING_STRATEGY_EVALUATED,
    HR_SCALING_STRATEGY_VALIDATION_FAILED,
)

logger = get_logger(__name__)
_DEFAULT_SAFETY_MARGIN: Final[float] = 0.9
_DEFAULT_HEADROOM_FRACTION: Final[float] = 0.6

_NAME = NotBlankStr("budget_cap")
_ACTION_TYPES = frozenset(
    {ScalingActionType.HOLD, ScalingActionType.NO_OP},
)


class BudgetCapStrategy:
    """Budget-aware scaling strategy.

    Emits both PRUNE and HOLD when burn rate exceeds the safety
    margin -- PRUNE targets the last agent in the ID list (cost-based
    selection is a future enhancement), and HOLD blocks hires from
    lower-priority strategies via the conflict resolver. Emits HOLD
    only (no PRUNE) when burn is between headroom and safety margin.
    Emits nothing when under headroom.

    Args:
        safety_margin: Burn rate fraction above which to prune and
            block hires.
        headroom_fraction: Burn rate fraction below which hires are
            allowed without a HOLD block.
    """

    def __init__(
        self,
        *,
        safety_margin: float = _DEFAULT_SAFETY_MARGIN,
        headroom_fraction: float = _DEFAULT_HEADROOM_FRACTION,
    ) -> None:
        if not 0.0 < safety_margin <= 1.0:
            msg = f"safety_margin must be in (0, 1], got {safety_margin}"
            logger.warning(
                HR_SCALING_STRATEGY_VALIDATION_FAILED,
                strategy="BudgetCapStrategy",
                field="safety_margin",
                value=safety_margin,
            )
            raise ValueError(msg)
        if not 0.0 <= headroom_fraction <= 1.0:
            msg = f"headroom_fraction must be in [0, 1], got {headroom_fraction}"
            logger.warning(
                HR_SCALING_STRATEGY_VALIDATION_FAILED,
                strategy="BudgetCapStrategy",
                field="headroom_fraction",
                value=headroom_fraction,
            )
            raise ValueError(msg)
        if headroom_fraction >= safety_margin:
            msg = (
                f"headroom_fraction ({headroom_fraction}) "
                f"must be < safety_margin ({safety_margin})"
            )
            logger.warning(
                HR_SCALING_STRATEGY_VALIDATION_FAILED,
                strategy="BudgetCapStrategy",
                field="margin_order",
                headroom_fraction=headroom_fraction,
                safety_margin=safety_margin,
            )
            raise ValueError(msg)
        self._safety_margin = safety_margin
        self._headroom_fraction = headroom_fraction

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
        """Evaluate budget signals and propose decisions.

        Args:
            context: Aggregated company state snapshot.

        Returns:
            PRUNE, HOLD, or empty tuple.
        """
        now = datetime.now(UTC)

        burn_signal = next(
            (s for s in context.budget_signals if s.name == "burn_rate_percent"),
            None,
        )
        if burn_signal is None:
            logger.info(
                HR_SCALING_STRATEGY_EVALUATED,
                strategy="budget_cap",
                decisions=0,
                reason="no_burn_signal",
            )
            return ()

        burn_fraction = burn_signal.value / 100.0
        budget_signals = (burn_signal,)

        if burn_fraction >= self._safety_margin:
            # Over budget -- block hires. A cost-aware prune target
            # selection is a future enhancement; until then we only
            # emit HOLD to prevent new spending rather than pruning
            # an arbitrary agent.
            decisions: list[ScalingDecision] = [
                ScalingDecision(
                    action_type=ScalingActionType.HOLD,
                    source_strategy=ScalingStrategyName.BUDGET_CAP,
                    rationale=NotBlankStr(
                        f"burn rate {burn_fraction:.0%} exceeds safety "
                        f"margin {self._safety_margin:.0%} -- blocking hires"
                    ),
                    confidence=1.0,
                    signals=budget_signals,
                    created_at=now,
                ),
            ]
            logger.info(
                HR_SCALING_STRATEGY_EVALUATED,
                strategy="budget_cap",
                decisions=len(decisions),
                action="hold",
                burn_fraction=burn_fraction,
            )
            return tuple(decisions)

        if burn_fraction > self._headroom_fraction:
            # Between headroom and safety -- block hires.
            decision = ScalingDecision(
                action_type=ScalingActionType.HOLD,
                source_strategy=ScalingStrategyName.BUDGET_CAP,
                rationale=NotBlankStr(
                    f"burn rate {burn_fraction:.0%} above headroom "
                    f"{self._headroom_fraction:.0%} -- blocking hires"
                ),
                confidence=1.0,
                signals=budget_signals,
                created_at=now,
            )
            logger.info(
                HR_SCALING_STRATEGY_EVALUATED,
                strategy="budget_cap",
                decisions=1,
                action="hold",
                burn_fraction=burn_fraction,
            )
            return (decision,)

        logger.info(
            HR_SCALING_STRATEGY_EVALUATED,
            strategy="budget_cap",
            decisions=0,
            burn_fraction=burn_fraction,
        )
        return ()
