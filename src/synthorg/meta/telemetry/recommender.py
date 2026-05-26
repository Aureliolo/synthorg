"""Threshold recommendation generator.

Analyzes cross-deployment patterns and suggests threshold
adjustments for built-in rules based on observed outcomes.
"""

import copy
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr
from synthorg.meta.telemetry.models import ThresholdRecommendation
from synthorg.observability import get_logger
from synthorg.observability.events.cross_deployment import (
    XDEPLOY_RECOMMENDATION_GENERATED,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.meta.telemetry.models import AggregatedPattern
    from synthorg.meta.telemetry.protocol import AnalyticsCollector

logger = get_logger(__name__)
_DEFAULT_MIN_DEPLOYMENTS: Final[int] = 3
_DEFAULT_MIN_OBSERVATIONS: Final[int] = 10

# Pattern-level thresholds that gate relax / tighten recommendations.
# Empirically tuned from v0.6.x rollout data: 0.7 approval / success
# marks "proposals are landing cleanly" (relax candidate), 0.3 approval
# marks "proposals are being routinely rejected" (tighten candidate).
# Both bounds are deliberate -- adjust here rather than duplicating
# inline in the recommendation logic below.
_HIGH_APPROVAL_RATE: Final[float] = 0.7
_HIGH_SUCCESS_RATE: Final[float] = 0.7
_LOW_APPROVAL_RATE: Final[float] = 0.3

# Built-in rule defaults keyed by source rule name. Each value is the
# current shipped default for the configurable threshold that the rule
# consults at runtime; the recommender proposes adjustments relative to
# these numbers.
_QUALITY_DROP_DEFAULT: Final[float] = 5.0
_SUCCESS_RATE_DEFAULT: Final[float] = 0.7
_BUDGET_OVERRUN_DAYS_DEFAULT: Final[float] = 14.0
_COORDINATION_RATIO_DEFAULT: Final[float] = 0.4
_OVERHEAD_PCT_DEFAULT: Final[float] = 35.0
_STRAGGLER_GAP_RATIO_DEFAULT: Final[float] = 2.0
_REDUNDANCY_RATE_DEFAULT: Final[float] = 0.3
_SCALING_FAILURE_RATE_DEFAULT: Final[float] = 0.5
_ERROR_FINDINGS_DEFAULT: Final[float] = 10.0

# Adjustment coefficients for relax / tighten recommendations.
_ADJUSTMENT_BASE: Final[float] = 0.1
_ADJUSTMENT_CONFIDENCE_COEF: Final[float] = 0.1
_RELAX_CONFIDENCE_DIVISOR: Final[float] = 10.0
_TIGHTEN_CONFIDENCE_BASE: Final[float] = 0.5
_TIGHTEN_CONFIDENCE_DIVISOR: Final[float] = 20.0

# Mapping of rule names to their configurable threshold fields
# and current defaults. Used to generate concrete recommendations.
_RULE_THRESHOLD_MAP: Mapping[str, tuple[str, float]] = MappingProxyType(
    copy.deepcopy(
        {
            "quality_declining": ("quality_drop_threshold", _QUALITY_DROP_DEFAULT),
            "success_rate_drop": ("success_rate_threshold", _SUCCESS_RATE_DEFAULT),
            "budget_overrun": (
                "days_until_exhausted_threshold",
                _BUDGET_OVERRUN_DAYS_DEFAULT,
            ),
            "coordination_cost_ratio": (
                "coordination_ratio_threshold",
                _COORDINATION_RATIO_DEFAULT,
            ),
            "coordination_overhead": (
                "overhead_pct_threshold",
                _OVERHEAD_PCT_DEFAULT,
            ),
            "straggler_bottleneck": (
                "straggler_gap_ratio_threshold",
                _STRAGGLER_GAP_RATIO_DEFAULT,
            ),
            "redundancy": ("redundancy_rate_threshold", _REDUNDANCY_RATE_DEFAULT),
            "scaling_failure": (
                "scaling_failure_rate_threshold",
                _SCALING_FAILURE_RATE_DEFAULT,
            ),
            "error_spike": ("error_findings_threshold", _ERROR_FINDINGS_DEFAULT),
        },
    ),
)


class DefaultThresholdRecommender:
    """Generates threshold recommendations from aggregated patterns.

    Analyzes cross-deployment patterns and recommends threshold
    adjustments when consistent outcomes are observed:

    - High approval + high success rate: threshold may be too
      conservative (fires correctly, proposals succeed). Recommend
      relaxing slightly.
    - Low approval rate: threshold may be too aggressive (fires
      often but humans reject). Recommend tightening.

    Args:
        min_deployments: Minimum unique deployments for pattern
            inclusion (from config ``min_deployments_for_pattern``).
        min_observations: Minimum events for recommendations
            (from config ``recommendation_min_observations``).
    """

    def __init__(
        self,
        *,
        min_deployments: int = _DEFAULT_MIN_DEPLOYMENTS,
        min_observations: int = _DEFAULT_MIN_OBSERVATIONS,
    ) -> None:
        self._min_deployments = min_deployments
        self._min_observations = min_observations

    async def get_recommendations(
        self,
        *,
        collector: AnalyticsCollector,
    ) -> tuple[ThresholdRecommendation, ...]:
        """Generate recommendations from collected data.

        Args:
            collector: Collector to query patterns from.

        Returns:
            Threshold recommendations sorted by confidence.
        """
        patterns = await collector.query_patterns(
            min_deployments=self._min_deployments,
        )
        recommendations: list[ThresholdRecommendation] = []

        for pattern in patterns:
            rec = self._evaluate_pattern(pattern)
            if rec is not None:
                recommendations.append(rec)

        recommendations.sort(key=lambda r: -r.confidence)

        if recommendations:
            logger.info(
                XDEPLOY_RECOMMENDATION_GENERATED,
                count=len(recommendations),
            )

        return tuple(recommendations)

    def _evaluate_pattern(
        self,
        pattern: AggregatedPattern,
    ) -> ThresholdRecommendation | None:
        """Evaluate a single pattern for threshold recommendation.

        Args:
            pattern: Aggregated cross-deployment pattern.

        Returns:
            A recommendation, or None if no adjustment is warranted.
        """
        threshold_info = _RULE_THRESHOLD_MAP.get(pattern.source_rule)
        if threshold_info is None:
            return None
        metric_name, current_default = threshold_info
        has_enough = pattern.total_events >= self._min_observations
        if not has_enough:
            return None

        if (
            pattern.approval_rate >= _HIGH_APPROVAL_RATE
            and pattern.success_rate >= _HIGH_SUCCESS_RATE
        ):
            return self._recommend_relax(pattern, metric_name, current_default)

        if pattern.decision_count >= 1 and pattern.approval_rate <= _LOW_APPROVAL_RATE:
            return self._recommend_tighten(pattern, metric_name, current_default)

        return None

    def _recommend_relax(
        self,
        pattern: AggregatedPattern,
        metric_name: str,
        current_default: float,
    ) -> ThresholdRecommendation:
        """Build a recommendation to relax a too-conservative threshold.

        Returns:
            ``ThresholdRecommendation`` instance.
        """
        adjustment = _ADJUSTMENT_BASE + (
            _ADJUSTMENT_CONFIDENCE_COEF * pattern.avg_confidence
        )
        recommended = current_default * (1.0 + adjustment)
        confidence = min(
            pattern.avg_confidence,
            pattern.deployment_count / _RELAX_CONFIDENCE_DIVISOR,
            1.0,
        )
        rationale = (
            f"Rule '{pattern.source_rule}' has "
            f"{pattern.approval_rate:.0%} approval and "
            f"{pattern.success_rate:.0%} success across "
            f"{pattern.deployment_count} deployments. "
            f"Threshold may be too conservative."
        )
        return _build_recommendation(
            pattern,
            metric_name,
            current_default,
            recommended,
            confidence,
            rationale,
        )

    def _recommend_tighten(
        self,
        pattern: AggregatedPattern,
        metric_name: str,
        current_default: float,
    ) -> ThresholdRecommendation:
        """Build a recommendation to tighten a too-aggressive threshold.

        Returns:
            ``ThresholdRecommendation`` instance.
        """
        adjustment = _ADJUSTMENT_BASE + (
            _ADJUSTMENT_CONFIDENCE_COEF * (1.0 - pattern.avg_confidence)
        )
        recommended = current_default * (1.0 - adjustment)
        confidence = min(
            _TIGHTEN_CONFIDENCE_BASE
            + (pattern.deployment_count / _TIGHTEN_CONFIDENCE_DIVISOR),
            1.0,
        )
        rationale = (
            f"Rule '{pattern.source_rule}' has only "
            f"{pattern.approval_rate:.0%} approval across "
            f"{pattern.deployment_count} deployments. "
            f"Threshold may be too aggressive."
        )
        return _build_recommendation(
            pattern,
            metric_name,
            current_default,
            recommended,
            confidence,
            rationale,
        )


def _build_recommendation(  # noqa: PLR0913
    pattern: AggregatedPattern,
    metric_name: str,
    current_default: float,
    recommended: float,
    confidence: float,
    rationale: str,
) -> ThresholdRecommendation:
    """Construct a ThresholdRecommendation from computed values.

    Returns:
        ``ThresholdRecommendation`` instance.
    """
    return ThresholdRecommendation(
        rule_name=NotBlankStr(pattern.source_rule),
        metric_name=NotBlankStr(metric_name),
        current_default=current_default,
        recommended_value=round(recommended, 4),
        confidence=confidence,
        based_on_deployments=pattern.deployment_count,
        based_on_observations=pattern.total_events,
        rationale=NotBlankStr(rationale),
    )
