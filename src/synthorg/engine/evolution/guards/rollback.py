"""RollbackGuard -- monitors post-adaptation performance for regression."""

from typing import TYPE_CHECKING

from synthorg.engine.evolution.models import AdaptationDecision, AdaptationProposal
from synthorg.observability import get_logger
from synthorg.observability.events.evolution import (
    EVOLUTION_GUARD_INVALID_CONFIG,
    EVOLUTION_ROLLBACK_TRIGGERED,
)

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class RollbackGuard:
    """Monitors post-adaptation performance for regression.

    The guard's ``evaluate()`` method always approves (pre-adaptation check).
    The ``check_regression()`` method is called post-adaptation to monitor
    for performance degradation and trigger rollback if needed.
    """

    def __init__(
        self,
        window_tasks: int = 20,
        regression_threshold: float = 0.1,
    ) -> None:
        """Initialize RollbackGuard.

        Args:
            window_tasks: Number of tasks to observe post-adaptation.
            regression_threshold: Quality drop threshold (0-1) to
                trigger rollback.  Default 0.1 = 10% quality drop;
                tighter than humans typically notice but loose enough
                to absorb evaluation-noise without thrashing.

        Raises:
            ValueError: If ``window_tasks`` is not a positive integer
                (``bool`` rejected explicitly because ``isinstance(True,
                int)`` is True), or if ``regression_threshold`` is
                non-finite (``NaN`` / ``+inf`` / ``-inf``) or outside
                ``[0, 1]``.  A non-positive window silently disables
                rollback monitoring; a non-finite threshold disables
                the guard; an out-of-range threshold inverts its
                meaning -- all three surface here.
        """
        import math  # noqa: PLC0415

        if not isinstance(window_tasks, int) or isinstance(window_tasks, bool):
            msg = f"window_tasks must be an integer, got {window_tasks!r}"
            logger.warning(
                EVOLUTION_GUARD_INVALID_CONFIG,
                guard_name="rollback",
                field="window_tasks",
                value=str(window_tasks),
                constraint="must be int (not bool)",
            )
            raise ValueError(msg)  # noqa: TRY004 -- consistent with sibling validators
        if window_tasks <= 0:
            msg = f"window_tasks must be > 0, got {window_tasks!r}"
            logger.warning(
                EVOLUTION_GUARD_INVALID_CONFIG,
                guard_name="rollback",
                field="window_tasks",
                value=window_tasks,
                constraint="must be > 0",
            )
            raise ValueError(msg)
        if not isinstance(regression_threshold, int | float) or isinstance(
            regression_threshold, bool
        ):
            msg = (
                f"regression_threshold must be a real number in "
                f"[0, 1], got {regression_threshold!r}"
            )
            logger.warning(
                EVOLUTION_GUARD_INVALID_CONFIG,
                guard_name="rollback",
                field="regression_threshold",
                value=str(regression_threshold),
                constraint="must be real number (not bool)",
            )
            raise ValueError(msg)  # noqa: TRY004 -- consistent with sibling validators
        if not math.isfinite(regression_threshold):
            msg = (
                f"regression_threshold must be a finite number in "
                f"[0, 1], got {regression_threshold!r}"
            )
            logger.warning(
                EVOLUTION_GUARD_INVALID_CONFIG,
                guard_name="rollback",
                field="regression_threshold",
                value=str(regression_threshold),
                constraint="must be finite",
            )
            raise ValueError(msg)
        if regression_threshold < 0.0 or regression_threshold > 1.0:
            msg = (
                f"regression_threshold must be in [0, 1], got {regression_threshold!r}"
            )
            logger.warning(
                EVOLUTION_GUARD_INVALID_CONFIG,
                guard_name="rollback",
                field="regression_threshold",
                value=regression_threshold,
                constraint="must be in [0, 1]",
            )
            raise ValueError(msg)
        self._window_tasks = window_tasks
        self._regression_threshold = regression_threshold

    @property
    def name(self) -> str:
        """Return guard name."""
        return "RollbackGuard"

    async def evaluate(
        self,
        proposal: AdaptationProposal,
    ) -> AdaptationDecision:
        """Evaluate the proposal (pre-adaptation check).

        Always approves. Post-adaptation rollback monitoring is done via
        ``check_regression()``.

        Args:
            proposal: The adaptation proposal to evaluate.

        Returns:
            Always approves.
        """
        return AdaptationDecision(
            proposal_id=proposal.id,
            approved=True,
            guard_name=self.name,
            reason="Pre-adaptation check passed; post-adaptation monitoring enabled",
        )

    async def check_regression(
        self,
        agent_id: NotBlankStr,
        baseline_quality: float,
        current_quality: float,
    ) -> bool:
        """Check for performance regression post-adaptation.

        Args:
            agent_id: Target agent.
            baseline_quality: Quality score before adaptation.
            current_quality: Quality score after adaptation.

        Returns:
            True if regression detected, False otherwise.
        """
        quality_drop = baseline_quality - current_quality
        has_regression = quality_drop >= self._regression_threshold

        if has_regression:
            logger.warning(
                EVOLUTION_ROLLBACK_TRIGGERED,
                agent_id=agent_id,
                baseline_quality=baseline_quality,
                current_quality=current_quality,
                drop=quality_drop,
                threshold=self._regression_threshold,
            )

        return has_regression
