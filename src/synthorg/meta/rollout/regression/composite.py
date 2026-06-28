"""Tiered regression detector.

Combines threshold (Layer 1) and statistical (Layer 2) detectors.
Layer 1 fires instantly for catastrophic regression.
Layer 2 fires after the observation window for subtle degradation.
"""

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    OrgSignalSnapshot,
    RegressionResult,
    RegressionThresholds,
    RegressionVerdict,
)
from synthorg.meta.rollout.regression.statistical import (
    StatisticalDetector,
)
from synthorg.meta.rollout.regression.threshold import (
    ThresholdDetector,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_REGRESSION_STATISTICAL,
    META_REGRESSION_THRESHOLD_BREACH,
)

logger = get_logger(__name__)


class TieredRegressionDetector:
    """Two-layer regression detection.

    Layer 1 (threshold) checks for immediate catastrophic regression.
    Layer 2 (statistical) checks for subtle, statistically
    significant degradation after the observation window.

    Args:
        threshold_detector: Layer 1 detector.
        statistical_detector: Layer 2 detector.
    """

    def __init__(
        self,
        *,
        threshold_detector: ThresholdDetector | None = None,
        statistical_detector: StatisticalDetector | None = None,
    ) -> None:
        self._threshold = threshold_detector or ThresholdDetector()
        self._statistical = statistical_detector or StatisticalDetector()

    @property
    def name(self) -> NotBlankStr:
        """Detector name."""
        return NotBlankStr("tiered")

    async def check(
        self,
        *,
        baseline: OrgSignalSnapshot,
        current: OrgSignalSnapshot,
        thresholds: RegressionThresholds,
    ) -> RegressionResult:
        """Run both detection layers.

        Layer 1 takes precedence -- if a threshold breach is
        detected, it returns immediately without running Layer 2.

        Args:
            baseline: Signal snapshot from before the change.
            current: Signal snapshot from after the change.
            thresholds: Configurable degradation thresholds.

        Returns:
            Regression result from whichever layer fires first.
        """
        # Layer 1: threshold check (instant).
        l1_result = await self._threshold.check(
            baseline=baseline,
            current=current,
            thresholds=thresholds,
        )
        if l1_result.verdict == RegressionVerdict.THRESHOLD_BREACH:
            logger.warning(
                META_REGRESSION_THRESHOLD_BREACH,
                metric=l1_result.breached_metric,
                baseline=l1_result.baseline_value,
                current=l1_result.current_value,
            )
            return l1_result

        # Layer 2: statistical check (after observation window).
        l2_result = await self._statistical.check(
            baseline=baseline,
            current=current,
            thresholds=thresholds,
        )
        if l2_result.verdict == RegressionVerdict.STATISTICAL_REGRESSION:
            logger.warning(
                META_REGRESSION_STATISTICAL,
                metric=l2_result.breached_metric,
                p_value=l2_result.p_value,
            )
            return l2_result

        return RegressionResult(
            verdict=RegressionVerdict.NO_REGRESSION,
        )
