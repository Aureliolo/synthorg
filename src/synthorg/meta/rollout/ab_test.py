"""A/B test rollout strategy.

Splits the live roster into control and treatment groups, applies the
proposal to the treatment group, then samples per-agent metrics over
the observation window. The comparator declares a winner when Welch's
t-test finds statistical significance and the mean quality improvement
exceeds the configured threshold.
"""

import asyncio
import hashlib
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    ImprovementProposal,
    RegressionThresholds,
    RegressionVerdict,
    RolloutOutcome,
    RolloutResult,
)
from synthorg.meta.protocol import ProposalApplier, RegressionDetector
from synthorg.meta.rollout.ab_comparator import ABTestComparator
from synthorg.meta.rollout.ab_models import (
    ABTestComparison,
    ABTestGroup,
    ABTestVerdict,
    GroupAssignment,
    GroupMetrics,
)
from synthorg.meta.rollout.group_aggregator import (
    GroupSamples,
    GroupSignalAggregator,
)
from synthorg.meta.rollout.roster import NoOpOrgRoster, OrgRoster
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_ABTEST_GROUPS_ASSIGNED,
    META_ABTEST_OBSERVATION_STARTED,
    META_ROLLOUT_COMPLETED,
    META_ROLLOUT_FAILED,
    META_ROLLOUT_OBSERVATION_COMPLETED,
    META_ROLLOUT_OBSERVATION_TICK,
    META_ROLLOUT_STARTED,
)

logger = get_logger(__name__)

_DEFAULT_CONTROL_FRACTION: Final[float] = 0.5
_DEFAULT_MIN_AGENTS_PER_GROUP: Final[int] = 5
_DEFAULT_MIN_OBSERVATIONS_PER_GROUP: Final[int] = 10
_DEFAULT_IMPROVEMENT_THRESHOLD: Final[float] = 0.15
_DEFAULT_SIGNIFICANCE_LEVEL: Final[float] = 0.05
_DEFAULT_CHECK_INTERVAL_HOURS: Final[float] = 4.0


class _NullGroupAggregator:
    """Aggregator returning no samples. Used as a safe default."""

    async def aggregate_for_agents(
        self,
        *,
        agent_ids: tuple[NotBlankStr, ...],
        since: datetime,
        until: datetime,
    ) -> GroupSamples:
        """Return aggregate for agents."""
        _ = agent_ids, since, until
        return GroupSamples()


class ABTestRollout:
    """A/B test rollout: split org, apply to treatment, compare.

    Splits agents into control (unchanged) and treatment (proposal
    applied) groups using deterministic hash-based assignment.
    During the observation window the strategy samples per-agent
    metrics on each tick; mid-window regressions exit early, and the
    final tick's comparison produces the verdict.

    Args:
        control_fraction: Fraction of agents for control (default 0.5).
        min_agents_per_group: Minimum agents required per group.
        min_observations_per_group: Minimum sample size before Welch runs.
        improvement_threshold: Minimum practical improvement ratio.
        significance_level: Welch's t-test alpha.
        comparator: Comparator instance (injectable for testing).
        clock: Clock for sleeping and timestamping.
        roster: Source of the live agent list.
        group_aggregator: Collects per-group samples during observation.
        check_interval_hours: Polling cadence inside the window.
        thresholds: Regression thresholds for catastrophic short-circuit.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        control_fraction: float = _DEFAULT_CONTROL_FRACTION,
        min_agents_per_group: int = _DEFAULT_MIN_AGENTS_PER_GROUP,
        min_observations_per_group: int = _DEFAULT_MIN_OBSERVATIONS_PER_GROUP,
        improvement_threshold: float = _DEFAULT_IMPROVEMENT_THRESHOLD,
        significance_level: float = _DEFAULT_SIGNIFICANCE_LEVEL,
        comparator: ABTestComparator | None = None,
        clock: Clock | None = None,
        roster: OrgRoster | None = None,
        group_aggregator: GroupSignalAggregator | None = None,
        check_interval_hours: float = _DEFAULT_CHECK_INTERVAL_HOURS,
        thresholds: RegressionThresholds | None = None,
    ) -> None:
        if control_fraction <= 0.0 or control_fraction >= 1.0:
            msg = "control_fraction must be in the range (0, 1) exclusive."
            raise ValueError(msg)
        if min_agents_per_group < 1:
            msg = "min_agents_per_group must be >= 1."
            raise ValueError(msg)
        if check_interval_hours <= 0.0:
            msg = "check_interval_hours must be positive"
            raise ValueError(msg)
        self._control_fraction = control_fraction
        self._min_agents_per_group = min_agents_per_group
        self._comparator = comparator or ABTestComparator(
            min_observations=min_observations_per_group,
            improvement_threshold=improvement_threshold,
            significance_level=significance_level,
        )
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._roster: OrgRoster = roster or NoOpOrgRoster()
        self._group_aggregator: GroupSignalAggregator = (
            group_aggregator or _NullGroupAggregator()
        )
        self._check_interval_hours = check_interval_hours
        self._thresholds = thresholds or RegressionThresholds()

    @property
    def name(self) -> NotBlankStr:
        """Strategy name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("ab_test")

    async def execute(
        self,
        *,
        proposal: ImprovementProposal,
        applier: ProposalApplier,
        detector: RegressionDetector,
    ) -> RolloutResult:
        """Execute A/B test rollout.

        Returns:
            ``RolloutResult`` instance.
        """
        _ = detector  # A/B uses comparator rather than RegressionDetector.
        logger.info(
            META_ROLLOUT_STARTED,
            strategy="ab_test",
            proposal_id=str(proposal.id),
            control_fraction=self._control_fraction,
            observation_hours=proposal.observation_window_hours,
        )

        agent_ids = await self._roster.list_agent_ids()
        assignment = ABTestRollout.assign_groups(
            agent_ids=agent_ids,
            proposal_id=proposal.id,
            control_fraction=self._control_fraction,
        )
        logger.info(
            META_ABTEST_GROUPS_ASSIGNED,
            proposal_id=str(proposal.id),
            total_agents=len(agent_ids),
            control_count=len(assignment.control_agent_ids),
            treatment_count=len(assignment.treatment_agent_ids),
        )
        if (
            len(assignment.control_agent_ids) < self._min_agents_per_group
            or len(assignment.treatment_agent_ids) < self._min_agents_per_group
        ):
            return RolloutResult(
                proposal_id=proposal.id,
                outcome=RolloutOutcome.INCONCLUSIVE,
                observation_hours_elapsed=0.0,
                details="insufficient agents for A/B test groups",
            )

        apply_result = await applier.apply(proposal)
        if not apply_result.success:
            logger.warning(
                META_ROLLOUT_FAILED,
                strategy="ab_test",
                proposal_id=str(proposal.id),
                error=apply_result.error_message,
            )
            return RolloutResult(
                proposal_id=proposal.id,
                outcome=RolloutOutcome.FAILED,
                observation_hours_elapsed=0.0,
                details=apply_result.error_message,
            )

        return await self._observe_and_compare(
            proposal=proposal,
            assignment=assignment,
        )

    async def _aggregate_tick(
        self,
        *,
        assignment: GroupAssignment,
        window_start: datetime,
        window_end: datetime,
    ) -> ABTestComparison:
        """Aggregate both groups for one tick and return the comparison.

        Fans out control + treatment aggregation into a ``TaskGroup``,
        wraps the samples in ``GroupMetrics``, and hands them to the
        comparator. Kept as a thin helper so ``_observe_and_compare``
        stays under the 50-line budget and the aggregation shape lives
        in exactly one place.

        Returns:
            ``ABTestComparison`` instance.
        """
        async with asyncio.TaskGroup() as tg:
            control_task = tg.create_task(
                self._group_aggregator.aggregate_for_agents(
                    agent_ids=assignment.control_agent_ids,
                    since=window_start,
                    until=window_end,
                ),
            )
            treatment_task = tg.create_task(
                self._group_aggregator.aggregate_for_agents(
                    agent_ids=assignment.treatment_agent_ids,
                    since=window_start,
                    until=window_end,
                ),
            )
        control_metrics = _samples_to_metrics(
            control_task.result(),
            ABTestGroup.CONTROL,
        )
        treatment_metrics = _samples_to_metrics(
            treatment_task.result(),
            ABTestGroup.TREATMENT,
        )
        return await self._comparator.compare(
            control=control_metrics,
            treatment=treatment_metrics,
            thresholds=self._thresholds,
        )

    def _early_exit_regressed(
        self,
        proposal: ImprovementProposal,
        elapsed: float,
        comparison: ABTestComparison,
    ) -> RolloutResult:
        """Return the REGRESSED result for a mid-window treatment drop.

        Returns:
            ``RolloutResult`` instance.
        """
        outcome, verdict = _map_verdict(comparison.verdict)
        logger.warning(
            META_ROLLOUT_FAILED,
            strategy="ab_test",
            proposal_id=str(proposal.id),
            reason="treatment_regressed_mid_window",
            elapsed_hours=elapsed,
        )
        return RolloutResult(
            proposal_id=proposal.id,
            outcome=outcome,
            regression_verdict=verdict,
            observation_hours_elapsed=elapsed,
        )

    def _finalize_observation(
        self,
        proposal: ImprovementProposal,
        elapsed: float,
        last_comparison: ABTestComparison | None,
    ) -> RolloutResult:
        """Map the terminal comparison into a ``RolloutResult``.

        Returns:
            ``RolloutResult`` instance.
        """
        logger.info(
            META_ROLLOUT_OBSERVATION_COMPLETED,
            strategy="ab_test",
            proposal_id=str(proposal.id),
            observation_hours_elapsed=elapsed,
        )
        if last_comparison is None:
            return RolloutResult(
                proposal_id=proposal.id,
                outcome=RolloutOutcome.INCONCLUSIVE,
                observation_hours_elapsed=elapsed,
                details="observation window produced no comparisons",
            )
        outcome, verdict = _map_verdict(last_comparison.verdict)
        logger.info(
            META_ROLLOUT_COMPLETED,
            strategy="ab_test",
            proposal_id=str(proposal.id),
            outcome=outcome.value,
            ab_verdict=last_comparison.verdict.value,
        )
        return RolloutResult(
            proposal_id=proposal.id,
            outcome=outcome,
            regression_verdict=verdict,
            observation_hours_elapsed=elapsed,
        )

    async def _observe_and_compare(
        self,
        *,
        proposal: ImprovementProposal,
        assignment: GroupAssignment,
    ) -> RolloutResult:
        """Run the observation loop and return the verdict.

        Returns:
            ``RolloutResult`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        logger.info(
            META_ABTEST_OBSERVATION_STARTED,
            proposal_id=str(proposal.id),
            observation_hours=proposal.observation_window_hours,
            check_interval_hours=self._check_interval_hours,
        )
        observation_hours = float(proposal.observation_window_hours)
        # Fast-fail on non-positive windows / intervals so callers see a
        # clear misconfiguration error instead of a silent zero-tick
        # INCONCLUSIVE result. Matches the guard in
        # ``_observation.observe_until_verdict``.
        if observation_hours <= 0.0:
            msg = f"observation_window_hours must be positive; got {observation_hours}"
            raise ValueError(msg)
        if self._check_interval_hours <= 0.0:
            msg = (
                "check_interval_hours must be positive so elapsed advances "
                f"each tick; got {self._check_interval_hours}"
            )
            raise ValueError(msg)
        elapsed = 0.0
        last_comparison: ABTestComparison | None = None
        while elapsed < observation_hours:
            remaining = observation_hours - elapsed
            step_hours = min(self._check_interval_hours, remaining)
            await self._clock.sleep(step_hours * 3600.0)
            elapsed += step_hours
            window_end = self._clock.now()
            window_start = window_end - timedelta(hours=elapsed)
            comparison = await self._aggregate_tick(
                assignment=assignment,
                window_start=window_start,
                window_end=window_end,
            )
            last_comparison = comparison
            logger.info(
                META_ROLLOUT_OBSERVATION_TICK,
                strategy="ab_test",
                proposal_id=str(proposal.id),
                elapsed_hours=elapsed,
                verdict=comparison.verdict.value,
            )
            if comparison.verdict == ABTestVerdict.TREATMENT_REGRESSED:
                return self._early_exit_regressed(
                    proposal,
                    elapsed,
                    comparison,
                )

        return self._finalize_observation(proposal, elapsed, last_comparison)

    @staticmethod
    def assign_groups(
        agent_ids: tuple[NotBlankStr, ...],
        proposal_id: UUID,
        control_fraction: float,
    ) -> GroupAssignment:
        """Deterministically assign agents to control/treatment.

        Uses SHA-256 hash of ``agent_id:proposal_id`` to assign
        each agent. The hash is stable across runs for the same
        inputs, producing reproducible group splits.

        Returns:
            ``GroupAssignment`` instance.
        """
        control: list[NotBlankStr] = []
        treatment: list[NotBlankStr] = []
        pid_str = str(proposal_id)
        for agent_id in agent_ids:
            digest = hashlib.sha256(
                f"{agent_id}:{pid_str}".encode(),
            ).hexdigest()
            bucket = int(digest[:8], 16) / 0x100000000
            if bucket < control_fraction:
                control.append(agent_id)
            else:
                treatment.append(agent_id)
        return GroupAssignment(
            proposal_id=proposal_id,
            control_agent_ids=tuple(control),
            treatment_agent_ids=tuple(treatment),
            control_fraction=control_fraction,
        )


def _samples_to_metrics(
    samples: GroupSamples,
    group: ABTestGroup,
) -> GroupMetrics:
    """Wrap aligned sample tuples in a ``GroupMetrics``.

    ``agent_count`` reflects agents that actually contributed samples
    (``samples.agent_ids``), not everyone who was assigned to the
    group. The aggregator drops agents missing metrics, so reporting
    the assigned count would overstate the effective sample size and
    let Welch think it had more data than it does.

    Returns:
        ``GroupMetrics`` instance.
    """
    return GroupMetrics(
        group=group,
        agent_count=len(samples.agent_ids),
        quality_samples=samples.quality_samples,
        success_samples=samples.success_samples,
        spend_samples=samples.spend_samples,
    )


def _map_verdict(
    verdict: ABTestVerdict,
) -> tuple[RolloutOutcome, RegressionVerdict | None]:
    """Map ABTestVerdict to RolloutOutcome + RegressionVerdict.

    Returns:
        The configured value when present, ``None`` otherwise.
    """
    if verdict == ABTestVerdict.TREATMENT_WINS:
        return RolloutOutcome.SUCCESS, RegressionVerdict.NO_REGRESSION
    if verdict in (
        ABTestVerdict.TREATMENT_REGRESSED,
        ABTestVerdict.CONTROL_WINS,
    ):
        return (
            RolloutOutcome.REGRESSED,
            RegressionVerdict.STATISTICAL_REGRESSION,
        )
    return RolloutOutcome.INCONCLUSIVE, None
