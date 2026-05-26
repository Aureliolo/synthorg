"""Conflict resolver guard.

Priority-ordered resolution of competing scaling decisions.
Budget cap HOLD decisions block hires from lower-priority
strategies.
"""

import copy
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.enums import ScalingActionType, ScalingStrategyName
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_SCALING_GUARD_APPLIED

if TYPE_CHECKING:
    from synthorg.hr.scaling.models import ScalingDecision

logger = get_logger(__name__)

# Default priority: lower number = higher priority.
DEFAULT_PRIORITY: dict[str, int] = {
    ScalingStrategyName.BUDGET_CAP.value: 0,
    ScalingStrategyName.PERFORMANCE_PRUNING.value: 1,
    ScalingStrategyName.SKILL_GAP.value: 2,
    ScalingStrategyName.WORKLOAD.value: 3,
}

_LOWEST_PRIORITY: Final[int] = 999


def _decision_key(decision: ScalingDecision) -> str:
    """Build a semantic dedup key for a decision.

    Targeted decisions (PRUNE) key on target_agent_id. Non-targeted
    decisions (HIRE) key on the role/department/skills tuple so
    duplicate hire proposals collapse to the highest-priority one.

    Returns:
        Result of type ``str``.
    """
    if decision.target_agent_id is not None:
        return f"target:{decision.target_agent_id}"
    parts = (
        str(decision.action_type),
        str(decision.target_role or ""),
        str(decision.target_department or ""),
        ",".join(sorted(str(s) for s in decision.target_skills)),
    )
    return "semantic:" + "|".join(parts)


class ConflictResolver:
    """Resolves conflicts between competing scaling decisions.

    Lower priority numbers win when decisions conflict on the same
    target (priority 0 beats priority 1, etc). Budget HOLD decisions
    block HIRE decisions from lower-priority strategies.

    Args:
        priority: Strategy name to priority mapping (lower = higher).
            Keys must be the string values of ``ScalingStrategyName``
            members (e.g. ``"workload"``).
    """

    def __init__(
        self,
        *,
        priority: dict[str, int] | None = None,
    ) -> None:
        base = DEFAULT_PRIORITY if priority is None else priority
        self._priority = MappingProxyType(copy.deepcopy(base))

    @property
    def name(self) -> NotBlankStr:
        """Guard identifier."""
        return NotBlankStr("conflict_resolver")

    def set_priority(self, priority: dict[str, int]) -> None:
        """Update the priority mapping at runtime."""
        self._priority = MappingProxyType(copy.deepcopy(priority))

    def _priority_for(self, decision: ScalingDecision) -> int:
        """Return the priority rank for a decision's strategy.

        Returns:
            Result of type ``int``.
        """
        return self._priority.get(decision.source_strategy.value, _LOWEST_PRIORITY)

    async def filter(
        self,
        decisions: tuple[ScalingDecision, ...],
    ) -> tuple[ScalingDecision, ...]:
        """Resolve conflicts and return filtered decisions.

        Args:
            decisions: Incoming decisions from all strategies.

        Returns:
            Filtered decisions with conflicts resolved.
        """
        if not decisions:
            return ()

        # Find the highest-priority HOLD (if any) -- this blocks
        # HIRE decisions from lower-priority strategies.
        hold_priority = min(
            (
                self._priority_for(d)
                for d in decisions
                if d.action_type == ScalingActionType.HOLD
            ),
            default=_LOWEST_PRIORITY + 1,
        )

        # Single-pass deduplication: keep the best decision per
        # semantic key. For targeted decisions (PRUNE), the key is
        # the target_agent_id. For non-targeted HIRE decisions, the
        # key is the role/department/skills tuple so duplicate hire
        # proposals for the same role collapse.
        best_by_key: dict[str, ScalingDecision] = {}
        chosen_hold: ScalingDecision | None = None

        for decision in decisions:
            # Track the highest-priority HOLD so it survives in the output.
            if decision.action_type == ScalingActionType.HOLD:
                if chosen_hold is None or self._priority_for(
                    decision,
                ) < self._priority_for(chosen_hold):
                    chosen_hold = decision
                continue

            # Block HIRE from lower-priority strategies when HOLD is active.
            if (
                decision.action_type == ScalingActionType.HIRE
                and self._priority_for(decision) > hold_priority
            ):
                logger.info(
                    HR_SCALING_GUARD_APPLIED,
                    guard="conflict_resolver",
                    action="blocked_hire",
                    strategy=decision.source_strategy.value,
                    reason="hold_from_higher_priority",
                )
                continue

            key = _decision_key(decision)
            existing = best_by_key.get(key)
            if existing is None or self._priority_for(decision) < self._priority_for(
                existing
            ):
                best_by_key[key] = decision

        final_list = list(best_by_key.values())
        if chosen_hold is not None:
            final_list.append(chosen_hold)
        final = tuple(final_list)

        logger.info(
            HR_SCALING_GUARD_APPLIED,
            guard="conflict_resolver",
            input_count=len(decisions),
            output_count=len(final),
        )
        return final
