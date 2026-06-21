"""Pure mapping helpers for the A/B rollout strategy.

Factored out of ``ab_test`` so the strategy module holds the observation
loop and persistence orchestration while these stateless projections
(group-samples to metrics, A/B verdict to rollout outcome) live in one
small leaf that takes no dependency back on the strategy.
"""

from synthorg.meta.models import RegressionVerdict, RolloutOutcome
from synthorg.meta.rollout.ab_models import ABTestGroup, ABTestVerdict, GroupMetrics
from synthorg.meta.rollout.group_aggregator import GroupSamples


def samples_to_metrics(
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


def map_verdict(
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
