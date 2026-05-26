"""Cooldown guard.

Drops decisions that fall within a cooldown window from a
recent same-type action on the same target.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_SCALING_GUARD_APPLIED

if TYPE_CHECKING:
    from synthorg.hr.scaling.models import ScalingDecision

logger = get_logger(__name__)
_DEFAULT_COOLDOWN_SECONDS: Final[int] = 3600


class CooldownGuard:
    """Per-action-type cooldown enforcement.

    Drops decisions where the same action type was recently
    executed on the same target (or globally for HOLD).

    Args:
        cooldown_seconds: Seconds to wait between same-type actions.
    """

    def __init__(
        self,
        *,
        cooldown_seconds: int = _DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        if cooldown_seconds < 0:
            msg = f"cooldown_seconds must be >= 0, got {cooldown_seconds}"
            logger.warning(
                HR_SCALING_GUARD_APPLIED,
                guard="cooldown",
                action="init_validation_failed",
                cooldown_seconds=cooldown_seconds,
            )
            raise ValueError(msg)
        self._cooldown = cooldown_seconds
        self._last_action: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    @property
    def name(self) -> NotBlankStr:
        """Guard identifier."""
        return NotBlankStr("cooldown")

    async def filter(
        self,
        decisions: tuple[ScalingDecision, ...],
    ) -> tuple[ScalingDecision, ...]:
        """Filter decisions through cooldown enforcement.

        Args:
            decisions: Incoming decisions.

        Returns:
            Decisions that pass the cooldown check.
        """
        now = datetime.now(UTC)
        result: list[ScalingDecision] = []
        reserved: list[str] = []

        async with self._lock:
            # Prune stale entries before evaluating decisions.
            cutoff = now - timedelta(seconds=self._cooldown)
            stale = [k for k, v in self._last_action.items() if v < cutoff]
            for k in stale:
                del self._last_action[k]

            for decision in decisions:
                key = self._make_key(decision)
                last = self._last_action.get(key)

                if last is not None:
                    elapsed = (now - last).total_seconds()
                    if elapsed < self._cooldown:
                        logger.info(
                            HR_SCALING_GUARD_APPLIED,
                            guard="cooldown",
                            action="dropped",
                            key=key,
                            elapsed_seconds=int(elapsed),
                            cooldown_seconds=self._cooldown,
                        )
                        continue

                # Tentatively reserve the slot so concurrent filter()
                # calls within the same async loop see this decision
                # and drop duplicates. If execution fails, callers
                # should invoke release_reservation(decision) to undo.
                self._last_action[key] = now
                reserved.append(key)
                result.append(decision)

        return tuple(result)

    async def release_reservation(self, decision: ScalingDecision) -> None:
        """Release a tentative cooldown reservation on failure.

        Called by ScalingService when execution fails so a failed
        attempt does not consume the cooldown window.
        """
        async with self._lock:
            key = self._make_key(decision)
            existed = key in self._last_action
            self._last_action.pop(key, None)
            logger.debug(
                HR_SCALING_GUARD_APPLIED,
                guard="cooldown",
                action="reservation_released",
                key=key,
                existed=existed,
            )

    async def record_action(self, decision: ScalingDecision) -> None:
        """Record that an action was executed for cooldown tracking.

        Args:
            decision: The decision that was executed.
        """
        async with self._lock:
            key = self._make_key(decision)
            self._last_action[key] = datetime.now(UTC)

    @staticmethod
    def _make_key(decision: ScalingDecision) -> str:
        """Make key.

        Returns:
            Result of type ``str``.
        """
        if decision.target_agent_id is not None:
            target = f"agent:{decision.target_agent_id}"
        elif decision.target_role is not None:
            parts = [
                f"role:{decision.target_role}",
                f"dept:{decision.target_department or ''}",
                "skills:" + ",".join(sorted(str(s) for s in decision.target_skills)),
            ]
            target = "|".join(parts)
        else:
            target = "global"
        return f"{decision.action_type}:{target}"
