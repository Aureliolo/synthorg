"""A/B test group metrics comparator.

Compares control vs treatment group metrics using a two-layer
approach: threshold short-circuit for catastrophic regression, then
Welch's unequal-variance t-test over the per-agent ``quality_samples``
for real statistical significance.
"""

import math
from typing import TYPE_CHECKING, Final

from synthorg.meta.rollout.ab_models import (
    ABTestComparison,
    ABTestVerdict,
    GroupMetrics,
)
from synthorg.meta.rollout.regression.welch import (
    InsufficientDataError,
    ZeroVarianceError,
    welch_t_test,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_ABTEST_INCONCLUSIVE,
    META_ABTEST_TREATMENT_REGRESSED,
    META_ABTEST_WINNER_DECLARED,
)

if TYPE_CHECKING:
    from synthorg.meta.models import RegressionThresholds

logger = get_logger(__name__)

_MIN_SAMPLES_FOR_VARIANCE: Final[int] = 2
_DEFAULT_MIN_OBSERVATIONS: Final[int] = 10
_DEFAULT_IMPROVEMENT_THRESHOLD: Final[float] = 0.15
_DEFAULT_SIGNIFICANCE_LEVEL: Final[float] = 0.05


class ABTestComparator:
    """Compares control vs treatment group metrics.

    Layer 1: Threshold check -- treatment catastrophically worse
    on any primary metric triggers immediate TREATMENT_REGRESSED.

    Layer 2: Statistical + practical significance -- declares
    TREATMENT_WINS only when Welch's t-test on ``quality_samples``
    rejects the null (``p < significance_level``) AND the mean
    quality improvement exceeds ``improvement_threshold``. Both
    gates are required: a stat-sig difference of 0.1 on a 10-point
    scale is probably not worth the rollout.

    Args:
        min_observations: Minimum metric samples per group
            before comparison is meaningful.
        improvement_threshold: Minimum practical improvement
            ratio (treatment vs control mean) to declare
            treatment as winner.
        significance_level: Welch's t-test alpha (default 0.05).
    """

    def __init__(
        self,
        *,
        min_observations: int = _DEFAULT_MIN_OBSERVATIONS,
        improvement_threshold: float = _DEFAULT_IMPROVEMENT_THRESHOLD,
        significance_level: float = _DEFAULT_SIGNIFICANCE_LEVEL,
    ) -> None:
        if not 0.0 < significance_level < 1.0:
            logger.warning(
                META_ABTEST_INCONCLUSIVE,
                reason="invalid_significance_level",
                significance_level=significance_level,
            )
            msg = "significance_level must be in (0, 1)"
            raise ValueError(msg)
        self._min_observations = min_observations
        self._improvement_threshold = improvement_threshold
        self._significance_level = significance_level

    async def compare(
        self,
        *,
        control: GroupMetrics,
        treatment: GroupMetrics,
        thresholds: RegressionThresholds,
    ) -> ABTestComparison:
        """Compare control and treatment group metrics.

        Args:
            control: Metrics from the control group.
            treatment: Metrics from the treatment group.
            thresholds: Regression thresholds for breach detection.

        Returns:
            Comparison result with verdict, effect size, and p-value.
        """
        if _insufficient_observations(
            control,
            treatment,
            self._min_observations,
        ):
            return _build_insufficient_result(
                control,
                treatment,
                self._min_observations,
            )

        regressed = _check_regressions(control, treatment, thresholds)
        if regressed:
            return _build_regression_result(
                control,
                treatment,
                regressed,
            )

        effect, p_value = _compute_effect(control, treatment)
        practical = _practical_improvement(control, treatment)
        if (
            p_value < self._significance_level
            and practical > self._improvement_threshold
        ):
            return _build_winner_result(
                control,
                treatment,
                effect,
                p_value,
            )

        return _build_no_difference_result(
            control,
            treatment,
            effect,
            p_value,
        )


def _insufficient_observations(
    control: GroupMetrics,
    treatment: GroupMetrics,
    min_obs: int,
) -> bool:
    """Check if either group has fewer samples than required."""
    return (
        len(control.quality_samples) < min_obs
        or len(treatment.quality_samples) < min_obs
    )


def _build_insufficient_result(
    control: GroupMetrics,
    treatment: GroupMetrics,
    min_obs: int,
) -> ABTestComparison:
    """Build INCONCLUSIVE result for insufficient observations."""
    logger.info(
        META_ABTEST_INCONCLUSIVE,
        reason="insufficient_observations",
        control_obs=control.observation_count,
        treatment_obs=treatment.observation_count,
        min_required=min_obs,
    )
    return ABTestComparison(
        verdict=ABTestVerdict.INCONCLUSIVE,
        control_metrics=control,
        treatment_metrics=treatment,
    )


def _build_regression_result(
    control: GroupMetrics,
    treatment: GroupMetrics,
    regressed: list[str],
) -> ABTestComparison:
    """Build TREATMENT_REGRESSED result."""
    logger.warning(
        META_ABTEST_TREATMENT_REGRESSED,
        regressed_metrics=list(regressed),
    )
    return ABTestComparison(
        verdict=ABTestVerdict.TREATMENT_REGRESSED,
        control_metrics=control,
        treatment_metrics=treatment,
        regressed_metrics=tuple(regressed),
    )


def _build_winner_result(
    control: GroupMetrics,
    treatment: GroupMetrics,
    effect: float,
    p_value: float,
) -> ABTestComparison:
    """Build TREATMENT_WINS result."""
    logger.info(
        META_ABTEST_WINNER_DECLARED,
        winner="treatment",
        effect_size=effect,
        p_value=p_value,
    )
    return ABTestComparison(
        verdict=ABTestVerdict.TREATMENT_WINS,
        control_metrics=control,
        treatment_metrics=treatment,
        effect_size=effect,
        p_value=p_value,
    )


def _build_no_difference_result(
    control: GroupMetrics,
    treatment: GroupMetrics,
    effect: float,
    p_value: float,
) -> ABTestComparison:
    """Build INCONCLUSIVE result for no significant difference."""
    logger.info(
        META_ABTEST_INCONCLUSIVE,
        reason="no_significant_difference",
        effect_size=effect,
    )
    return ABTestComparison(
        verdict=ABTestVerdict.INCONCLUSIVE,
        control_metrics=control,
        treatment_metrics=treatment,
        effect_size=effect,
        p_value=p_value,
    )


def _check_regressions(
    control: GroupMetrics,
    treatment: GroupMetrics,
    thresholds: RegressionThresholds,
) -> list[str]:
    """Check if treatment regressed beyond thresholds."""
    regressed: list[str] = []

    # Quality drop (lower is worse).
    if control.avg_quality_score > 0.0:
        drop = (
            control.avg_quality_score - treatment.avg_quality_score
        ) / control.avg_quality_score
        if drop > thresholds.quality_drop:
            regressed.append("quality")

    # Success rate drop (lower is worse).
    if control.avg_success_rate > 0.0:
        drop = (
            control.avg_success_rate - treatment.avg_success_rate
        ) / control.avg_success_rate
        if drop > thresholds.success_rate_drop:
            regressed.append("success_rate")

    # Cost increase (higher is worse). Compare per-agent average spend
    # rather than raw totals so the comparison is robust to unequal
    # group sizes (a treatment arm with 2x agents is not 2x worse).
    control_n = control.observation_count
    treatment_n = treatment.observation_count
    if control_n > 0 and treatment_n > 0:
        control_avg_spend = control.total_spend / control_n
        treatment_avg_spend = treatment.total_spend / treatment_n
        if control_avg_spend == 0.0:
            if treatment_avg_spend > 0.0:
                regressed.append("cost")
        else:
            increase = (treatment_avg_spend - control_avg_spend) / control_avg_spend
            if increase > thresholds.cost_increase:
                regressed.append("cost")

    return regressed


def _compute_effect(
    control: GroupMetrics,
    treatment: GroupMetrics,
) -> tuple[float, float]:
    """Compute Welch's effect size (Cohen's d) and two-sided p-value.

    Uses the per-agent ``quality_samples`` from each group. When the
    test cannot be run (insufficient data, zero variance) the effect
    collapses to ``0.0`` and the p-value to ``1.0`` so downstream
    callers treat the comparison as inconclusive.

    Returns:
        Tuple of ``(cohen_d, p_two_sided)``. ``cohen_d`` is clamped
        to non-negative values because downstream only cares about
        improvement magnitude.
    """
    try:
        welch = welch_t_test(
            treatment.quality_samples,
            control.quality_samples,
        )
    except (InsufficientDataError, ZeroVarianceError) as exc:
        logger.warning(
            META_ABTEST_INCONCLUSIVE,
            reason="welch_test_unavailable",
            error=type(exc).__name__,
            control_samples=len(control.quality_samples),
            treatment_samples=len(treatment.quality_samples),
        )
        return 0.0, 1.0

    pooled_sd = _pooled_sd(
        control.quality_samples,
        treatment.quality_samples,
    )
    if pooled_sd == 0.0:
        return 0.0, welch.p_two_sided
    cohen_d = (
        sum(treatment.quality_samples) / len(treatment.quality_samples)
        - sum(control.quality_samples) / len(control.quality_samples)
    ) / pooled_sd
    return max(cohen_d, 0.0), welch.p_two_sided


def _pooled_sd(
    control: tuple[float, ...],
    treatment: tuple[float, ...],
) -> float:
    """Simple pooled standard deviation for Cohen's d."""
    n_a = len(control)
    n_b = len(treatment)
    if n_a < _MIN_SAMPLES_FOR_VARIANCE or n_b < _MIN_SAMPLES_FOR_VARIANCE:
        return 0.0
    mean_a = sum(control) / n_a
    mean_b = sum(treatment) / n_b
    ss_a = math.fsum((x - mean_a) ** 2 for x in control)
    ss_b = math.fsum((x - mean_b) ** 2 for x in treatment)
    pooled_var = (ss_a + ss_b) / (n_a + n_b - 2)
    return math.sqrt(pooled_var) if pooled_var > 0.0 else 0.0


def _practical_improvement(
    control: GroupMetrics,
    treatment: GroupMetrics,
) -> float:
    """Treatment mean quality as a fraction above control mean.

    Returns ``0.0`` if control has zero mean (treat as no reference).
    Negative values (treatment worse) also return ``0.0`` -- the
    caller uses this to gate winner declarations.
    """
    c = control.avg_quality_score
    t = treatment.avg_quality_score
    if c <= 0.0:
        return 0.0
    return max((t - c) / c, 0.0)
