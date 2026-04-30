"""Before/after rollout strategy with periodic regression checks.

Applies the proposal to the whole org, captures a baseline snapshot,
then samples the current signal snapshot at ``check_interval_hours``
over the proposal's ``observation_window_hours``. Regression verdicts
terminate the loop immediately. A clean window yields SUCCESS with
the observed elapsed time.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    ImprovementProposal,
    OrgSignalSnapshot,
    RegressionThresholds,
    RolloutOutcome,
    RolloutResult,
)
from synthorg.meta.rollout._observation import observe_until_verdict
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_ROLLOUT_FAILED,
    META_ROLLOUT_STARTED,
)

if TYPE_CHECKING:
    from synthorg.meta.protocol import ProposalApplier, RegressionDetector

logger = get_logger(__name__)

SnapshotBuilder = Callable[[], Awaitable[OrgSignalSnapshot]]
"""Coroutine producing the current org-wide signal snapshot."""


async def _default_snapshot_builder() -> OrgSignalSnapshot:
    """Fail loud when callers forget to wire a real snapshot builder.

    A fabricated zero snapshot would silently compare against real
    current data and produce misleading regression verdicts. Raising
    here surfaces the misconfiguration the moment a rollout tries to
    observe, rather than reporting false SUCCESS / REGRESSED.
    """
    msg = (
        "snapshot_builder is not wired: rollouts cannot observe without "
        "a real OrgSignalSnapshot source. Pass snapshot_builder=... to "
        "the rollout strategy (or to SelfImprovementService)."
    )
    raise RuntimeError(msg)


class BeforeAfterRollout:
    """Applies a proposal to the whole org with periodic regression checks.

    Args:
        clock: Clock for sleeping and timestamping (defaults to wall clock).
        snapshot_builder: Async callable returning the current snapshot.
        check_interval_hours: How often to poll the detector mid-window.
        thresholds: Regression thresholds forwarded to the detector.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        snapshot_builder: SnapshotBuilder | None = None,
        check_interval_hours: float = 4.0,
        thresholds: RegressionThresholds | None = None,
    ) -> None:
        if check_interval_hours <= 0.0:
            msg = "check_interval_hours must be positive"
            raise ValueError(msg)
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._snapshot_builder: SnapshotBuilder = (
            snapshot_builder or _default_snapshot_builder
        )
        self._check_interval_hours = check_interval_hours
        self._thresholds = thresholds or RegressionThresholds()

    @property
    def name(self) -> NotBlankStr:
        """Strategy name."""
        return NotBlankStr("before_after")

    async def execute(
        self,
        *,
        proposal: ImprovementProposal,
        applier: ProposalApplier,
        detector: RegressionDetector,
    ) -> RolloutResult:
        """Execute the before/after rollout with a real observation loop."""
        logger.info(
            META_ROLLOUT_STARTED,
            strategy="before_after",
            proposal_id=str(proposal.id),
            observation_hours=proposal.observation_window_hours,
            check_interval_hours=self._check_interval_hours,
        )

        try:
            baseline = await self._snapshot_builder()
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                META_ROLLOUT_FAILED,
                strategy="before_after",
                proposal_id=str(proposal.id),
                stage="baseline_capture",
                error=type(exc).__name__,
                details=str(exc),
            )
            return RolloutResult(
                proposal_id=proposal.id,
                outcome=RolloutOutcome.FAILED,
                observation_hours_elapsed=0.0,
                details=str(exc),
            )

        apply_result = await applier.apply(proposal)
        if not apply_result.success:
            logger.warning(
                META_ROLLOUT_FAILED,
                strategy="before_after",
                proposal_id=str(proposal.id),
                error=apply_result.error_message,
            )
            return RolloutResult(
                proposal_id=proposal.id,
                outcome=RolloutOutcome.FAILED,
                observation_hours_elapsed=0.0,
                details=apply_result.error_message,
            )

        try:
            return await self._observe_window(
                proposal=proposal,
                baseline=baseline,
                detector=detector,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                META_ROLLOUT_FAILED,
                strategy="before_after",
                proposal_id=str(proposal.id),
                stage="observation",
                error=type(exc).__name__,
                details=str(exc),
            )
            return RolloutResult(
                proposal_id=proposal.id,
                outcome=RolloutOutcome.FAILED,
                observation_hours_elapsed=0.0,
                details=str(exc),
            )

    async def _observe_window(
        self,
        *,
        proposal: ImprovementProposal,
        baseline: OrgSignalSnapshot,
        detector: RegressionDetector,
    ) -> RolloutResult:
        """Poll the detector until the observation window closes."""
        return await observe_until_verdict(
            proposal=proposal,
            baseline=baseline,
            detector=detector,
            clock=self._clock,
            snapshot_builder=self._snapshot_builder,
            check_interval_hours=self._check_interval_hours,
            thresholds=self._thresholds,
            strategy_name="before_after",
        )
