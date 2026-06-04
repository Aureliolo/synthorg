"""Rollout-execution mixin for the self-improvement service.

Validates rollout preconditions, executes the chosen rollout strategy
for an approved proposal, and runs tiered regression detection after a
successful rollout. The cycle / learning / facade surface lives in
``service``; this mixin owns only the rollout path.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING

from synthorg.meta.models import (
    ImprovementProposal,
    OrgSignalSnapshot,
    ProposalAltitude,
    ProposalStatus,
    RegressionThresholds,
    RegressionVerdict,
    RolloutOutcome,
    RolloutResult,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_ROLLOUT_PRECONDITION_FAILED,
    META_ROLLOUT_REGRESSION_DETECTED,
)

if TYPE_CHECKING:
    from synthorg.meta.config import SelfImprovementConfig
    from synthorg.meta.protocol import ProposalApplier, RolloutStrategy
    from synthorg.meta.rollout.regression.composite import (
        TieredRegressionDetector,
    )
    from synthorg.meta.telemetry.protocol import AnalyticsEmitter

logger = get_logger(__name__)


class SelfImprovementRolloutMixin:
    """Rollout execution + post-rollout regression detection.

    Relies on the concrete :class:`SelfImprovementService` to supply the
    config, appliers, rollout strategies, regression detector, and
    analytics emitter.
    """

    _config: SelfImprovementConfig
    _appliers: Mapping[ProposalAltitude, ProposalApplier]
    _rollout_strategies: Mapping[str, RolloutStrategy]
    _detector: TieredRegressionDetector
    _analytics_emitter: AnalyticsEmitter | None

    def _build_regression_thresholds(self) -> RegressionThresholds:
        """Build RegressionThresholds from the service config.

        Returns:
            ``RegressionThresholds`` instance.
        """
        rc = self._config.regression
        return RegressionThresholds(
            quality_drop=rc.quality_drop_threshold,
            cost_increase=rc.cost_increase_threshold,
            error_rate_increase=rc.error_rate_increase_threshold,
            success_rate_drop=rc.success_rate_drop_threshold,
        )

    async def execute_rollout(
        self,
        proposal: ImprovementProposal,
        *,
        baseline: OrgSignalSnapshot | None = None,
        current: OrgSignalSnapshot | None = None,
    ) -> RolloutResult:
        """Execute a rollout for an approved proposal.

        If ``baseline`` and ``current`` snapshots are provided, the
        tiered regression detector is invoked after the rollout
        completes.  On regression, the result is updated with the
        detection verdict.

        Args:
            proposal: The human-approved proposal.
            baseline: Signal snapshot taken before the rollout.
            current: Signal snapshot taken after the observation
                window completes.

        Returns:
            Rollout result (may include regression verdict).
        """
        applier, rollout = self._validate_rollout_preconditions(proposal)
        result = await rollout.execute(
            proposal=proposal,
            applier=applier,
            detector=self._detector,
        )
        result = await self._post_rollout_regression_check(
            result,
            proposal,
            baseline=baseline,
            current=current,
        )
        if self._analytics_emitter is not None:
            await self._analytics_emitter.emit_rollout(
                result,
                proposal=proposal,
            )
        return result

    def _validate_rollout_preconditions(
        self,
        proposal: ImprovementProposal,
    ) -> tuple[ProposalApplier, RolloutStrategy]:
        """Validate proposal status, applier, and strategy exist.

        Returns:
            Tuple of the declared element types.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if proposal.status is not ProposalStatus.APPROVED:
            logger.error(
                META_ROLLOUT_PRECONDITION_FAILED,
                proposal_id=str(proposal.id),
                reason="not_approved",
                status=proposal.status.value,
            )
            msg = (
                f"Proposal {proposal.id} must be approved before "
                f"rollout; current status is {proposal.status.value}"
            )
            raise ValueError(msg)
        applier = self._appliers.get(proposal.altitude)
        if applier is None:
            logger.error(
                META_ROLLOUT_PRECONDITION_FAILED,
                proposal_id=str(proposal.id),
                reason="no_applier",
                altitude=proposal.altitude.value,
            )
            msg = f"No applier for altitude {proposal.altitude}"
            raise ValueError(msg)
        strategy_name = proposal.rollout_strategy.value
        rollout = self._rollout_strategies.get(strategy_name)
        if rollout is None:
            logger.error(
                META_ROLLOUT_PRECONDITION_FAILED,
                proposal_id=str(proposal.id),
                reason="no_strategy",
                strategy=strategy_name,
            )
            msg = f"No rollout strategy '{strategy_name}'"
            raise ValueError(msg)
        return applier, rollout

    async def _post_rollout_regression_check(
        self,
        result: RolloutResult,
        proposal: ImprovementProposal,
        *,
        baseline: OrgSignalSnapshot | None,
        current: OrgSignalSnapshot | None,
    ) -> RolloutResult:
        """Run tiered regression detection after a successful rollout.

        Returns:
            ``RolloutResult`` instance.
        """
        if (
            baseline is None
            or current is None
            or result.outcome != RolloutOutcome.SUCCESS
        ):
            return result
        thresholds = self._build_regression_thresholds()
        regression = await self._detector.check(
            baseline=baseline,
            current=current,
            thresholds=thresholds,
        )
        if regression.verdict == RegressionVerdict.NO_REGRESSION:
            return result
        logger.warning(
            META_ROLLOUT_REGRESSION_DETECTED,
            proposal_id=str(proposal.id),
            verdict=regression.verdict.value,
            breached_metric=regression.breached_metric,
        )
        return result.model_copy(
            update={
                "outcome": RolloutOutcome.REGRESSED,
                "regression_verdict": regression.verdict,
                "details": (
                    f"Regression detected: {regression.verdict.value}"
                    f" on {regression.breached_metric or 'unknown'}"
                ),
            },
        )
