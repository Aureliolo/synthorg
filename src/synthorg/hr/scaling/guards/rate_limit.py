"""Rate limit guard.

Hard cap on the number of scaling actions per rolling window.
"""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.enums import ScalingActionType
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_SCALING_GUARD_APPLIED

if TYPE_CHECKING:
    from synthorg.hr.scaling.models import ScalingDecision

logger = get_logger(__name__)
_DEFAULT_MAX_HIRES_PER_DAY: Final[int] = 3


class RateLimitGuard:
    """Global rate limits on scaling actions.

    Drops decisions that would exceed the daily cap for their
    action type.

    Args:
        max_hires_per_day: Maximum hire decisions per 24h window.
        max_prunes_per_day: Maximum prune decisions per 24h window.
    """

    def __init__(
        self,
        *,
        max_hires_per_day: int = _DEFAULT_MAX_HIRES_PER_DAY,
        max_prunes_per_day: int = 1,
    ) -> None:
        self._limits: dict[str, int] = {
            str(ScalingActionType.HIRE): max_hires_per_day,
            str(ScalingActionType.PRUNE): max_prunes_per_day,
        }
        self._history: dict[str, list[datetime]] = {}
        self._lock = asyncio.Lock()

    @property
    def name(self) -> NotBlankStr:
        """Guard identifier."""
        return NotBlankStr("rate_limit")

    async def filter(
        self,
        decisions: tuple[ScalingDecision, ...],
    ) -> tuple[ScalingDecision, ...]:
        """Filter decisions through rate limit enforcement.

        Args:
            decisions: Incoming decisions.

        Returns:
            Decisions that don't exceed the daily cap.
        """
        now = datetime.now(UTC)
        cutoff = now.timestamp() - 86400
        result: list[ScalingDecision] = []
        accepted_in_batch: dict[str, int] = {}
        pruned_actions: set[str] = set()

        async with self._lock:
            for decision in decisions:
                action = str(decision.action_type)
                limit = self._limits.get(action)
                if limit is None:
                    result.append(decision)
                    continue

                # Prune old entries once per action type.
                if action not in pruned_actions:
                    history = self._history.get(action, [])
                    history = [t for t in history if t.timestamp() > cutoff]
                    self._history[action] = history
                    pruned_actions.add(action)
                else:
                    history = self._history.get(action, [])

                used = len(history) + accepted_in_batch.get(action, 0)
                if used >= limit:
                    logger.info(
                        HR_SCALING_GUARD_APPLIED,
                        guard="rate_limit",
                        action="dropped",
                        action_type=action,
                        count=used,
                        limit=limit,
                    )
                    continue

                result.append(decision)
                accepted_in_batch[action] = accepted_in_batch.get(action, 0) + 1

        return tuple(result)

    async def record_action(self, decision: ScalingDecision) -> None:
        """Record that an action was executed for rate tracking.

        Only records actions that have configured limits (HIRE/PRUNE).
        HOLD and NO_OP are not rate-limited and are ignored.

        Args:
            decision: The decision that was executed.
        """
        async with self._lock:
            action = str(decision.action_type)
            if action not in self._limits:
                return
            history = self._history.setdefault(action, [])
            history.append(datetime.now(UTC))
