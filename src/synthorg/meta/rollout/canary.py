"""Canary subset rollout strategy with real observation loop.

Selects a canary subset deterministically (hashed split), applies the
proposal, then observes canary vs baseline metrics over the
observation window. Mid-window regressions exit early; a clean window
yields SUCCESS with the observed elapsed time.
"""

import hashlib
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    ImprovementProposal,
    OrgSignalSnapshot,
    RegressionThresholds,
    RolloutOutcome,
    RolloutResult,
)
from synthorg.meta.protocol import ProposalApplier, RegressionDetector
from synthorg.meta.rollout._observation import (
    observe_until_verdict,
    with_applied_rollback_operations,
)
from synthorg.meta.rollout.before_after import (
    RolloutSnapshotBuilder,
    _default_snapshot_builder,
)
from synthorg.meta.rollout.roster import NoOpOrgRoster, OrgRoster
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_ROLLOUT_FAILED,
    META_ROLLOUT_STARTED,
)

logger = get_logger(__name__)
_DEFAULT_CANARY_FRACTION: Final[float] = 0.2
_DEFAULT_CHECK_INTERVAL_HOURS: Final[float] = 4.0


class CanarySubsetRollout:
    """Applies a proposal to a canary subset, then expands on success.

    Args:
        canary_fraction: Fraction of the live roster placed in the canary.
        clock: Clock for sleeping and timestamping.
        roster: Provides the live list of agent ids.
        snapshot_builder: Builds the current signal snapshot.
        check_interval_hours: Polling cadence inside the window.
        thresholds: Regression thresholds for the detector.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        canary_fraction: float = _DEFAULT_CANARY_FRACTION,
        clock: Clock | None = None,
        roster: OrgRoster | None = None,
        snapshot_builder: RolloutSnapshotBuilder | None = None,
        check_interval_hours: float = _DEFAULT_CHECK_INTERVAL_HOURS,
        thresholds: RegressionThresholds | None = None,
    ) -> None:
        if canary_fraction <= 0.0 or canary_fraction > 1.0:
            msg = "canary_fraction must be in the range (0, 1]."
            raise ValueError(msg)
        if check_interval_hours <= 0.0:
            msg = "check_interval_hours must be positive"
            raise ValueError(msg)
        self._canary_fraction = canary_fraction
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._roster: OrgRoster = roster or NoOpOrgRoster()
        self._snapshot_builder: RolloutSnapshotBuilder = (
            snapshot_builder or _default_snapshot_builder
        )
        self._check_interval_hours = check_interval_hours
        self._thresholds = thresholds or RegressionThresholds()

    @property
    def name(self) -> NotBlankStr:
        """Strategy name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("canary")

    async def execute(
        self,
        *,
        proposal: ImprovementProposal,
        applier: ProposalApplier,
        detector: RegressionDetector,
    ) -> RolloutResult:
        """Execute canary rollout with a real observation loop.

        Returns:
            ``RolloutResult`` instance.
        """
        agent_ids = await self._roster.list_agent_ids()
        canary_ids = _select_canary(
            agent_ids=agent_ids,
            proposal_id=str(proposal.id),
            fraction=self._canary_fraction,
        )
        logger.info(
            META_ROLLOUT_STARTED,
            strategy="canary",
            proposal_id=str(proposal.id),
            canary_fraction=self._canary_fraction,
            total_agents=len(agent_ids),
            canary_count=len(canary_ids),
            observation_hours=proposal.observation_window_hours,
        )

        baseline = await self._snapshot_builder()
        apply_result = await applier.apply(proposal)
        if not apply_result.success:
            logger.warning(
                META_ROLLOUT_FAILED,
                strategy="canary",
                proposal_id=str(proposal.id),
                error=apply_result.error_message,
            )
            return RolloutResult(
                proposal_id=proposal.id,
                outcome=RolloutOutcome.FAILED,
                observation_hours_elapsed=0.0,
                details=apply_result.error_message,
            )

        result = await self._observe_window(
            proposal=proposal,
            baseline=baseline,
            detector=detector,
        )
        return with_applied_rollback_operations(
            result, apply_result.rollback_operations
        )

    async def _observe_window(
        self,
        *,
        proposal: ImprovementProposal,
        baseline: OrgSignalSnapshot,
        detector: RegressionDetector,
    ) -> RolloutResult:
        """Poll the detector until the canary observation window closes.

        Returns:
            ``RolloutResult`` instance.
        """
        return await observe_until_verdict(
            proposal=proposal,
            baseline=baseline,
            detector=detector,
            clock=self._clock,
            snapshot_builder=self._snapshot_builder,
            check_interval_hours=self._check_interval_hours,
            thresholds=self._thresholds,
            strategy_name="canary",
        )


def _select_canary(
    *,
    agent_ids: tuple[NotBlankStr, ...],
    proposal_id: str,
    fraction: float,
) -> tuple[NotBlankStr, ...]:
    """Deterministic hash-based canary selection.

    Agents whose ``sha256(agent_id:proposal_id)`` bucket falls below
    ``fraction`` join the canary. Pure function, identical inputs
    produce identical splits across runs.

    Returns:
        Tuple of the declared element types.
    """
    canary: list[NotBlankStr] = []
    for agent_id in agent_ids:
        digest = hashlib.sha256(
            f"{agent_id}:{proposal_id}".encode(),
        ).hexdigest()
        bucket = int(digest[:8], 16) / 0x100000000
        if bucket < fraction:
            canary.append(agent_id)
    return tuple(canary)
