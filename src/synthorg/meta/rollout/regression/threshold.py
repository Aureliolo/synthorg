"""Threshold-based regression detector (Layer 1).

Fires instantly when any primary metric degrades beyond
a configurable threshold compared to baseline.
"""

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    OrgSignalSnapshot,
    RegressionResult,
    RegressionThresholds,
    RegressionVerdict,
)
from synthorg.observability import get_logger

logger = get_logger(__name__)


class ThresholdDetector:
    """Detects regression via configurable metric thresholds.

    Compares baseline and current signal snapshots. If any primary
    metric degrades beyond the configured threshold, returns a
    THRESHOLD_BREACH verdict for immediate auto-rollback.
    """

    @property
    def name(self) -> NotBlankStr:
        """Detector name."""
        return NotBlankStr("threshold")

    async def check(
        self,
        *,
        baseline: OrgSignalSnapshot,
        current: OrgSignalSnapshot,
        thresholds: RegressionThresholds,
    ) -> RegressionResult:
        """Check for threshold breaches.

        Args:
            baseline: Signal snapshot from before the change.
            current: Signal snapshot from after the change.
            thresholds: Configurable degradation thresholds.

        Returns:
            Regression result.
        """
        checks = [
            (
                "quality",
                baseline.performance.avg_quality_score,
                current.performance.avg_quality_score,
                thresholds.quality_drop,
                True,  # lower is worse
            ),
            (
                "success_rate",
                baseline.performance.avg_success_rate,
                current.performance.avg_success_rate,
                thresholds.success_rate_drop,
                True,
            ),
            (
                "cost",
                baseline.budget.total_spend,
                current.budget.total_spend,
                thresholds.cost_increase,
                False,  # higher is worse
            ),
        ]

        for metric, base_val, curr_val, threshold, lower_is_worse in checks:
            if base_val == 0.0:
                continue
            if lower_is_worse:
                drop = (base_val - curr_val) / base_val
                if drop > threshold:
                    return RegressionResult(
                        verdict=RegressionVerdict.THRESHOLD_BREACH,
                        breached_metric=metric,
                        baseline_value=base_val,
                        current_value=curr_val,
                        threshold=threshold,
                    )
            else:
                increase = (curr_val - base_val) / base_val
                if increase > threshold:
                    return RegressionResult(
                        verdict=RegressionVerdict.THRESHOLD_BREACH,
                        breached_metric=metric,
                        baseline_value=base_val,
                        current_value=curr_val,
                        threshold=threshold,
                    )

        return RegressionResult(verdict=RegressionVerdict.NO_REGRESSION)
