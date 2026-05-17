"""Statistical regression detector (Layer 2).

Runs Welch's unequal-variance t-test over per-agent / per-task samples
captured during the observation window. A regression fires when the
current window's quality or success samples are significantly worse
than the baseline, or when cost samples are significantly higher.

The detector queries samples through an injected
``StatisticalSampleSource`` keyed by the snapshot timestamps. When no
source is wired the detector yields ``INSUFFICIENT_DATA``: the
detector refuses to fire without data rather than guessing.
"""

import math
from datetime import datetime  # noqa: TC003 -- Pydantic needs at runtime
from enum import Enum
from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    OrgSignalSnapshot,
    RegressionResult,
    RegressionThresholds,
    RegressionVerdict,
)
from synthorg.meta.rollout.regression.welch import (
    InsufficientDataError,
    WelchResult,
    ZeroVarianceError,
    welch_t_test,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_REGRESSION_STATISTICAL,
    META_REGRESSION_STATISTICAL_INSUFFICIENT_DATA,
)

logger = get_logger(__name__)
_DEFAULT_MIN_DATA_POINTS: Final[int] = 10
_DEFAULT_SIGNIFICANCE_LEVEL: Final[float] = 0.05


class WindowSamples(BaseModel):
    """Raw per-sample values for the statistical detector.

    Each tuple is one observation per data point (per-agent or
    per-task, depending on the source). ``quality_samples`` and
    ``success_samples`` are compared in the direction "baseline
    better than current"; ``cost_samples`` in the direction
    "current higher than baseline".
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    quality_samples: tuple[float, ...] = ()
    success_samples: tuple[float, ...] = ()
    cost_samples: tuple[float, ...] = ()


@runtime_checkable
class StatisticalSampleSource(Protocol):
    """Returns the raw samples backing a snapshot window."""

    async def fetch_for_window(
        self,
        *,
        window_end: datetime,
    ) -> WindowSamples:
        """Return per-observation samples for the window ending at ``window_end``."""
        ...


class NoSampleSource:
    """Default source that never returns samples.

    Forces the detector to yield ``INSUFFICIENT_DATA`` until a real
    source is wired in. Keeps the detector honest by not inventing
    samples out of thin air.
    """

    async def fetch_for_window(
        self,
        *,
        window_end: datetime,
    ) -> WindowSamples:
        """Return empty samples regardless of the window."""
        _ = window_end
        return WindowSamples()


class _MetricCheckOutcome(Enum):
    """Sentinel distinguishing 'Welch ran cleanly' from 'could not run'.

    A metric that returns ``OK`` definitely did not regress (Welch
    accepted the sample and found either p >= alpha or the wrong
    direction). ``UNTESTABLE`` means the sample was too small / had
    zero combined variance / raised inside Welch; no conclusion at all.
    Preserving this distinction prevents the detector from collapsing
    "don't know yet" into "everything is fine".
    """

    OK = "ok"
    UNTESTABLE = "untestable"


class StatisticalDetector:
    """Layer 2 regression detector using Welch's t-test.

    Args:
        min_data_points: Minimum samples per arm before Welch runs.
        significance_level: Alpha for the two-sided hypothesis test.
        sample_source: Fetches raw samples keyed by snapshot time.
    """

    def __init__(
        self,
        *,
        min_data_points: int = _DEFAULT_MIN_DATA_POINTS,
        significance_level: float = _DEFAULT_SIGNIFICANCE_LEVEL,
        sample_source: StatisticalSampleSource | None = None,
    ) -> None:
        if min_data_points < 2:  # noqa: PLR2004 -- Welch requires n>=2
            msg = "min_data_points must be >= 2 for Welch's t-test"
            raise ValueError(msg)
        if not 0.0 < significance_level < 1.0:
            msg = "significance_level must be in (0, 1)"
            raise ValueError(msg)
        self._min_data_points = min_data_points
        self._alpha = significance_level
        self._source = sample_source or NoSampleSource()

    @property
    def name(self) -> NotBlankStr:
        """Detector name."""
        return NotBlankStr("statistical")

    async def check(
        self,
        *,
        baseline: OrgSignalSnapshot,
        current: OrgSignalSnapshot,
        thresholds: RegressionThresholds,
    ) -> RegressionResult:
        """Check for a statistically significant regression."""
        _ = baseline, current, thresholds  # alpha lives on the detector
        baseline_samples = await self._source.fetch_for_window(
            window_end=baseline.collected_at,
        )
        current_samples = await self._source.fetch_for_window(
            window_end=current.collected_at,
        )

        saw_ok = False
        for metric, base_tuple, curr_tuple, lower_is_worse in (
            (
                "quality",
                baseline_samples.quality_samples,
                current_samples.quality_samples,
                True,
            ),
            (
                "success_rate",
                baseline_samples.success_samples,
                current_samples.success_samples,
                True,
            ),
            (
                "cost",
                baseline_samples.cost_samples,
                current_samples.cost_samples,
                False,
            ),
        ):
            verdict = self._check_metric(
                metric=metric,
                baseline=base_tuple,
                current=curr_tuple,
                lower_is_worse=lower_is_worse,
            )
            if isinstance(verdict, RegressionResult):
                return verdict
            if verdict is _MetricCheckOutcome.OK:
                saw_ok = True

        if self._all_insufficient(baseline_samples, current_samples):
            logger.info(
                META_REGRESSION_STATISTICAL_INSUFFICIENT_DATA,
                min_required=self._min_data_points,
            )
            return RegressionResult(
                verdict=RegressionVerdict.INSUFFICIENT_DATA,
            )
        if not saw_ok:
            # Every metric was UNTESTABLE -- Welch could not run on
            # any of them (zero variance, too few samples, or
            # non-convergence). Surface that as INSUFFICIENT_DATA
            # instead of collapsing "don't know yet" into a clean
            # NO_REGRESSION that callers would treat as a pass.
            return RegressionResult(
                verdict=RegressionVerdict.INSUFFICIENT_DATA,
            )
        return RegressionResult(verdict=RegressionVerdict.NO_REGRESSION)

    def _check_metric(
        self,
        *,
        metric: str,
        baseline: tuple[float, ...],
        current: tuple[float, ...],
        lower_is_worse: bool,
    ) -> RegressionResult | _MetricCheckOutcome:
        """Run Welch on a single metric and return a verdict or sentinel.

        Returns a ``RegressionResult`` when the metric has regressed,
        ``_MetricCheckOutcome.OK`` when Welch ran and said no regression,
        or ``_MetricCheckOutcome.UNTESTABLE`` when Welch could not run
        (insufficient samples or zero variance). The caller uses the
        sentinel to distinguish "clean" from "not testable yet".
        """
        if (
            len(baseline) < self._min_data_points
            or len(current) < self._min_data_points
        ):
            return _MetricCheckOutcome.UNTESTABLE
        try:
            welch: WelchResult = welch_t_test(baseline, current)
        except (InsufficientDataError, ZeroVarianceError) as exc:
            logger.debug(
                META_REGRESSION_STATISTICAL_INSUFFICIENT_DATA,
                metric=metric,
                error=type(exc).__name__,
                baseline_samples=len(baseline),
                current_samples=len(current),
            )
            return _MetricCheckOutcome.UNTESTABLE
        base_value = math.fsum(baseline) / len(baseline)
        curr_value = math.fsum(current) / len(current)
        if welch.p_two_sided >= self._alpha:
            return _MetricCheckOutcome.OK
        if lower_is_worse:
            if curr_value >= base_value:
                return _MetricCheckOutcome.OK
        elif curr_value <= base_value:
            return _MetricCheckOutcome.OK
        logger.warning(
            META_REGRESSION_STATISTICAL,
            metric=metric,
            p_value=welch.p_two_sided,
            t=welch.t,
            df=welch.df,
            base_value=base_value,
            curr_value=curr_value,
        )
        return RegressionResult(
            verdict=RegressionVerdict.STATISTICAL_REGRESSION,
            breached_metric=NotBlankStr(metric),
            baseline_value=base_value,
            current_value=curr_value,
            p_value=welch.p_two_sided,
        )

    def _all_insufficient(
        self,
        baseline: WindowSamples,
        current: WindowSamples,
    ) -> bool:
        """True when every metric has fewer than ``min_data_points`` in one arm."""
        for base_tuple, curr_tuple in (
            (baseline.quality_samples, current.quality_samples),
            (baseline.success_samples, current.success_samples),
            (baseline.cost_samples, current.cost_samples),
        ):
            if (
                len(base_tuple) >= self._min_data_points
                and len(curr_tuple) >= self._min_data_points
            ):
                return False
        return True
