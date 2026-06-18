"""Shared observation-loop helper for before/after and canary rollouts.

Both strategies poll a detector at ``check_interval_hours`` over the
proposal's ``observation_window_hours`` and exit early on
``THRESHOLD_BREACH``. This module factors the identical polling body
into a single coroutine so each strategy only owns its strategy-name
log tag and baseline-capture semantics.
"""

from collections.abc import Awaitable, Callable
from typing import Final

from synthorg.core.clock import Clock
from synthorg.meta.models import (
    ImprovementProposal,
    OrgSignalSnapshot,
    RegressionResult,
    RegressionThresholds,
    RegressionVerdict,
    RolloutOutcome,
    RolloutResult,
)
from synthorg.meta.protocol import RegressionDetector
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_ROLLOUT_COMPLETED,
    META_ROLLOUT_OBSERVATION_COMPLETED,
    META_ROLLOUT_OBSERVATION_TICK,
    META_ROLLOUT_REGRESSION_DETECTED,
)

# Produces the current org-signal snapshot each observation tick. Defined
# here (the shared consumer) rather than imported from ``before_after`` so
# this leaf does not take a back-edge into a strategy module. Named
# distinctly from ``meta.signals.snapshot.SnapshotBuilder`` (the concrete
# aggregator class) since this is the zero-arg callable the strategies poll.
type RolloutSnapshotBuilder = Callable[[], Awaitable[OrgSignalSnapshot]]

logger = get_logger(__name__)

_SECONDS_PER_HOUR: Final[float] = 3600.0


def validate_window_and_interval(
    observation_hours: float,
    check_interval_hours: float,
) -> None:
    """Reject a non-positive observation window or check interval.

    Shared by :func:`observe_until_verdict` and the A/B rollout loop:
    both need a positive window and a positive interval so ``elapsed``
    advances each tick.

    Raises:
        ValueError: If either value is not strictly positive.
    """
    if observation_hours <= 0.0:
        msg = f"observation_window_hours must be positive; got {observation_hours}"
        raise ValueError(msg)
    if check_interval_hours <= 0.0:
        msg = (
            "check_interval_hours must be positive so elapsed advances "
            f"each tick; got {check_interval_hours}"
        )
        raise ValueError(msg)


def _regression_exit_result(
    *,
    result: RegressionResult,
    proposal: ImprovementProposal,
    elapsed: float,
    strategy_name: str,
) -> RolloutResult:
    """Build the early-exit result for a mid-window regression.

    Returns:
        ``RolloutResult`` instance.
    """
    logger.warning(
        META_ROLLOUT_REGRESSION_DETECTED,
        strategy=strategy_name,
        proposal_id=str(proposal.id),
        verdict=result.verdict.value,
        elapsed_hours=elapsed,
    )
    return RolloutResult(
        proposal_id=proposal.id,
        outcome=RolloutOutcome.REGRESSED,
        regression_verdict=result.verdict,
        observation_hours_elapsed=elapsed,
        details=(
            str(result.breached_metric) if result.breached_metric is not None else None
        ),
    )


def _window_closed_result(
    *,
    last_result: RegressionResult | None,
    proposal: ImprovementProposal,
    elapsed: float,
    strategy_name: str,
) -> RolloutResult:
    """Map the final verdict to an outcome after a clean window close.

    Returns:
        ``RolloutResult`` instance.
    """
    logger.info(
        META_ROLLOUT_OBSERVATION_COMPLETED,
        strategy=strategy_name,
        proposal_id=str(proposal.id),
        observation_hours_elapsed=elapsed,
    )
    # Preserve the final verdict so INSUFFICIENT_DATA / other non-regression
    # non-clean outcomes are not collapsed into SUCCESS. A window that closed
    # without any observation tick (no data) is INSUFFICIENT_DATA, not a clean
    # NO_REGRESSION pass, so it maps to INCONCLUSIVE rather than a false SUCCESS.
    final_verdict = (
        last_result.verdict
        if last_result is not None
        else RegressionVerdict.INSUFFICIENT_DATA
    )
    final_breached = last_result.breached_metric if last_result is not None else None
    if final_verdict == RegressionVerdict.NO_REGRESSION:
        outcome = RolloutOutcome.SUCCESS
    elif final_verdict == RegressionVerdict.INSUFFICIENT_DATA:
        # "Don't know yet" must not be reported as a regression.
        # Map to INCONCLUSIVE so callers can decide whether to extend
        # the window or abort rather than rolling back on no data.
        outcome = RolloutOutcome.INCONCLUSIVE
    else:
        outcome = RolloutOutcome.REGRESSED
    logger.info(
        META_ROLLOUT_COMPLETED,
        strategy=strategy_name,
        proposal_id=str(proposal.id),
        outcome=outcome.value,
        verdict=final_verdict.value,
    )
    return RolloutResult(
        proposal_id=proposal.id,
        outcome=outcome,
        regression_verdict=final_verdict,
        observation_hours_elapsed=elapsed,
        details=str(final_breached) if final_breached is not None else None,
    )


async def observe_until_verdict(  # noqa: PLR0913
    *,
    proposal: ImprovementProposal,
    baseline: OrgSignalSnapshot,
    detector: RegressionDetector,
    clock: Clock,
    snapshot_builder: RolloutSnapshotBuilder,
    check_interval_hours: float,
    thresholds: RegressionThresholds,
    strategy_name: str,
) -> RolloutResult:
    """Poll ``detector`` until the observation window closes or regresses.

    Exits early on ``THRESHOLD_BREACH`` or at the end of the window on
    ``STATISTICAL_REGRESSION``. A clean window yields SUCCESS.

    Args:
        proposal: The proposal under observation.
        baseline: Pre-apply signal snapshot for comparison.
        detector: Regression detector to query each tick.
        clock: Time source (sleep + now).
        snapshot_builder: Produces the current snapshot each tick.
        check_interval_hours: Interval between detector polls.
        thresholds: Regression thresholds forwarded to the detector.
        strategy_name: Identifies the caller in structured logs.

    Returns:
        ``RolloutResult`` instance.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    observation_hours = float(proposal.observation_window_hours)
    validate_window_and_interval(observation_hours, check_interval_hours)
    elapsed = 0.0
    last_result: RegressionResult | None = None
    while elapsed < observation_hours:
        remaining = observation_hours - elapsed
        step_hours = min(check_interval_hours, remaining)
        await clock.sleep(step_hours * _SECONDS_PER_HOUR)
        elapsed += step_hours
        current = await snapshot_builder()
        result = await detector.check(
            baseline=baseline,
            current=current,
            thresholds=thresholds,
        )
        last_result = result
        logger.info(
            META_ROLLOUT_OBSERVATION_TICK,
            strategy=strategy_name,
            proposal_id=str(proposal.id),
            elapsed_hours=elapsed,
            verdict=result.verdict.value,
        )
        if result.verdict == RegressionVerdict.THRESHOLD_BREACH or (
            elapsed >= observation_hours
            and result.verdict == RegressionVerdict.STATISTICAL_REGRESSION
        ):
            return _regression_exit_result(
                result=result,
                proposal=proposal,
                elapsed=elapsed,
                strategy_name=strategy_name,
            )

    return _window_closed_result(
        last_result=last_result,
        proposal=proposal,
        elapsed=elapsed,
        strategy_name=strategy_name,
    )
