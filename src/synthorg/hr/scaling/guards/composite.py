"""Composite guard -- chains guards sequentially."""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_SCALING_GUARD_APPLIED

if TYPE_CHECKING:
    from synthorg.hr.scaling.models import ScalingDecision
    from synthorg.hr.scaling.protocols import ScalingGuard

logger = get_logger(__name__)


class CompositeScalingGuard:
    """Chains multiple guards sequentially.

    Each guard receives the output of the previous one.

    Args:
        guards: Ordered tuple of guards to apply.
    """

    def __init__(
        self,
        *,
        guards: tuple[ScalingGuard, ...],
    ) -> None:
        self._guards = guards

    @property
    def name(self) -> NotBlankStr:
        """Guard identifier."""
        return NotBlankStr("composite")

    def get_guards(self) -> tuple[ScalingGuard, ...]:
        """Return the contained guards (read-only)."""
        return self._guards

    async def filter(
        self,
        decisions: tuple[ScalingDecision, ...],
    ) -> tuple[ScalingDecision, ...]:
        """Apply all guards sequentially.

        Args:
            decisions: Incoming decisions.

        Returns:
            Decisions filtered through all guards.
        """
        current = decisions
        for guard in self._guards:
            before = len(current)
            try:
                current = await guard.filter(current)
            except Exception:
                logger.error(
                    HR_SCALING_GUARD_APPLIED,
                    guard=str(guard.name),
                    action="guard_error",
                    input_count=before,
                )
                raise
            logger.debug(
                HR_SCALING_GUARD_APPLIED,
                guard=str(guard.name),
                input_count=before,
                output_count=len(current),
            )
        return current
