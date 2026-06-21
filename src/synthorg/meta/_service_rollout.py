"""Rollout-execution mixin for the self-improvement service.

Validates rollout preconditions, executes the chosen rollout strategy
for an approved proposal, and runs tiered regression detection after a
successful rollout. The cycle / learning / facade surface lives in
``service``; this mixin owns only the rollout path.
"""

from collections.abc import Mapping

from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.config import SelfImprovementConfig
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
from synthorg.meta.protocol import ProposalApplier, RolloutStrategy
from synthorg.meta.rollout.regression.composite import TieredRegressionDetector
from synthorg.meta.rollout.rollback import RollbackExecutor
from synthorg.meta.telemetry.protocol import AnalyticsEmitter
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_ROLLBACK_FAILED,
    META_ROLLBACK_STARTED,
    META_ROLLOUT_PRECONDITION_FAILED,
    META_ROLLOUT_REGRESSION_DETECTED,
)

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
    _rollback_executor: RollbackExecutor | None

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
        result = await self._dispatch_auto_rollback(result, proposal)
        if self._analytics_emitter is not None:
            await self._analytics_emitter.emit_rollout(
                result,
                proposal=proposal,
            )
        return result

    async def _dispatch_auto_rollback(
        self,
        result: RolloutResult,
        proposal: ImprovementProposal,
    ) -> RolloutResult:
        """Auto-roll-back a regressed rollout via the wired executor.

        Dispatches the applier-materialised inverse operations carried on
        the rollout result. On success the outcome flips to ``ROLLED_BACK``
        (keeping the regression verdict); on a failed or errored rollback
        the outcome stays ``REGRESSED`` with the failure surfaced in
        ``details`` so it is never silently swallowed. A no-op when no
        executor is wired, the rollout did not regress, or nothing was
        applied to reverse.

        Returns:
            The (possibly outcome-flipped) rollout result.
        """
        if (
            result.outcome is not RolloutOutcome.REGRESSED
            or self._rollback_executor is None
        ):
            return result
        operations = result.applied_rollback_operations
        if not operations:
            logger.warning(
                META_ROLLBACK_FAILED,
                proposal_id=str(proposal.id),
                reason="no_materialised_rollback_operations",
            )
            return result
        logger.info(
            META_ROLLBACK_STARTED,
            proposal_id=str(proposal.id),
            operations=len(operations),
        )
        base_details = result.details or "Regression detected"
        try:
            rollback_result = await self._rollback_executor.execute_operations(
                operations,
                proposal_id=proposal.id,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                META_ROLLBACK_FAILED,
                proposal_id=str(proposal.id),
                reason="auto_rollback_errored",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return result.model_copy(
                update={
                    "details": (
                        f"{base_details}; auto-rollback errored ({type(exc).__name__})"
                    ),
                },
            )
        if not rollback_result.success:
            return result.model_copy(
                update={
                    "details": (
                        f"{base_details}; auto-rollback failed: "
                        f"{rollback_result.error_message}"
                    ),
                },
            )
        return result.model_copy(
            update={
                "outcome": RolloutOutcome.ROLLED_BACK,
                "details": (
                    f"{base_details}; auto-rolled back "
                    f"{rollback_result.changes_applied} change(s)"
                ),
            },
        )

    def _validate_rollout_preconditions(
        self,
        proposal: ImprovementProposal,
    ) -> tuple[ProposalApplier, RolloutStrategy]:
        """Validate proposal status, applier, and strategy exist.

        Returns:
            Tuple of the resolved (applier, rollout strategy) for the proposal.

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
