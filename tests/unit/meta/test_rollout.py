"""Unit tests for meta-loop rollout and regression detection."""

import pytest

from synthorg.meta.models import (
    ApplyResult,
    ConfigChange,
    ImprovementProposal,
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    ProposalAltitude,
    ProposalRationale,
    RegressionResult,
    RegressionThresholds,
    RegressionVerdict,
    RollbackOperation,
    RollbackPlan,
    RolloutOutcome,
)
from synthorg.meta.rollout.before_after import BeforeAfterRollout
from synthorg.meta.rollout.canary import CanarySubsetRollout
from synthorg.meta.rollout.regression.composite import (
    TieredRegressionDetector,
)
from synthorg.meta.rollout.regression.statistical import (
    StatisticalDetector,
    StatisticalSampleSource,
    WindowSamples,
)
from synthorg.meta.rollout.regression.threshold import (
    ThresholdDetector,
)
from synthorg.meta.rollout.rollback import RollbackExecutor
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


# ── Helpers ────────────────────────────────────────────────────────


async def _async_snapshot_builder() -> OrgSignalSnapshot:
    """Snapshot builder used by rollout tests that want neutral baselines."""
    return _snap()


def _snap(
    quality: float = 7.5,
    success: float = 0.85,
    spend: float = 100.0,
) -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=quality,
            avg_success_rate=success,
            avg_collaboration_score=6.0,
            agent_count=10,
        ),
        budget=OrgBudgetSummary(
            total_spend=spend,
            productive_ratio=0.6,
            coordination_ratio=0.3,
            system_ratio=0.1,
            forecast_confidence=0.8,
            orchestration_overhead=0.5,
        ),
        coordination=OrgCoordinationSummary(),
        scaling=OrgScalingSummary(),
        errors=OrgErrorSummary(),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


def _proposal() -> ImprovementProposal:
    return ImprovementProposal(
        altitude=ProposalAltitude.CONFIG_TUNING,
        title="test",
        description="test",
        rationale=ProposalRationale(
            signal_summary="test",
            pattern_detected="test",
            expected_impact="test",
            confidence_reasoning="test",
        ),
        config_changes=(
            ConfigChange(
                path="a.b",
                old_value=1,
                new_value=2,
                description="d",
            ),
        ),
        rollback_plan=RollbackPlan(
            operations=(
                RollbackOperation(
                    operation_type="revert_config",
                    target="a.b",
                    previous_value=1,
                    description="revert a.b",
                ),
            ),
            validation_check="a.b equals 1",
        ),
        confidence=0.8,
    )


def _thresholds() -> RegressionThresholds:
    return RegressionThresholds()


# ── ThresholdDetector ──────────────────────────────────────────────


class TestThresholdDetector:
    """Threshold detector tests."""

    async def test_no_regression(self) -> None:
        detector = ThresholdDetector()
        result = await detector.check(
            baseline=_snap(quality=7.5),
            current=_snap(quality=7.4),
            thresholds=_thresholds(),
        )
        assert result.verdict == RegressionVerdict.NO_REGRESSION

    async def test_quality_breach(self) -> None:
        detector = ThresholdDetector()
        result = await detector.check(
            baseline=_snap(quality=8.0),
            current=_snap(quality=6.0),
            thresholds=RegressionThresholds(quality_drop=0.10),
        )
        assert result.verdict == RegressionVerdict.THRESHOLD_BREACH
        assert result.breached_metric == "quality"
        assert result.baseline_value == 8.0
        assert result.current_value == 6.0

    async def test_success_rate_breach(self) -> None:
        detector = ThresholdDetector()
        result = await detector.check(
            baseline=_snap(success=0.9),
            current=_snap(success=0.7),
            thresholds=RegressionThresholds(success_rate_drop=0.10),
        )
        assert result.verdict == RegressionVerdict.THRESHOLD_BREACH
        assert result.breached_metric == "success_rate"

    async def test_cost_increase_breach(self) -> None:
        detector = ThresholdDetector()
        result = await detector.check(
            baseline=_snap(spend=100.0),
            current=_snap(spend=130.0),
            thresholds=RegressionThresholds(cost_increase=0.20),
        )
        assert result.verdict == RegressionVerdict.THRESHOLD_BREACH
        assert result.breached_metric == "cost"

    async def test_no_breach_within_tolerance(self) -> None:
        detector = ThresholdDetector()
        result = await detector.check(
            baseline=_snap(quality=8.0),
            current=_snap(quality=7.5),
            thresholds=RegressionThresholds(quality_drop=0.10),
        )
        assert result.verdict == RegressionVerdict.NO_REGRESSION

    async def test_zero_baseline_skipped(self) -> None:
        detector = ThresholdDetector()
        result = await detector.check(
            baseline=_snap(quality=0.0),
            current=_snap(quality=5.0),
            thresholds=_thresholds(),
        )
        assert result.verdict == RegressionVerdict.NO_REGRESSION


# ── StatisticalDetector ────────────────────────────────────────────


class _FakeSampleSource:
    """StatisticalSampleSource that routes by call order.

    The first ``fetch_for_window`` call returns ``baseline``, the
    second returns ``current``, and subsequent calls return an empty
    ``WindowSamples``. Call-order driven so tests don't have to
    produce distinct ``window_end`` timestamps when two ``_snap()``
    helpers happen to share ``collected_at``.
    """

    def __init__(
        self,
        *,
        baseline: WindowSamples,
        current: WindowSamples,
    ) -> None:
        self._baseline = baseline
        self._current = current
        self._call_count = 0

    async def fetch_for_window(self, *, window_end: object) -> WindowSamples:
        _ = window_end
        self._call_count += 1
        if self._call_count == 1:
            return self._baseline
        if self._call_count == 2:
            return self._current
        return WindowSamples()


def _samples(values: tuple[float, ...]) -> WindowSamples:
    return WindowSamples(quality_samples=values)


class TestStatisticalDetector:
    """Statistical detector tests over Welch's t-test."""

    async def test_no_source_returns_insufficient_data(self) -> None:
        detector = StatisticalDetector()
        baseline = _snap(quality=8.0)
        current = _snap(quality=5.0)
        result = await detector.check(
            baseline=baseline,
            current=current,
            thresholds=_thresholds(),
        )
        assert result.verdict == RegressionVerdict.INSUFFICIENT_DATA

    async def test_no_regression_when_means_close(self) -> None:
        baseline = _snap(quality=7.5)
        current = _snap(quality=7.45)
        samples = tuple(7.5 + 0.05 * (i % 3 - 1) for i in range(20))
        detector = StatisticalDetector(
            min_data_points=10,
            significance_level=0.05,
            sample_source=_FakeSampleSource(
                baseline=_samples(samples),
                current=_samples(samples),
            ),
        )
        result = await detector.check(
            baseline=baseline,
            current=current,
            thresholds=_thresholds(),
        )
        assert result.verdict == RegressionVerdict.NO_REGRESSION

    async def test_significant_regression(self) -> None:
        baseline = _snap(quality=8.0)
        current = _snap(quality=5.0)
        baseline_samples = tuple(8.0 + 0.1 * (i % 3 - 1) for i in range(20))
        current_samples = tuple(5.0 + 0.1 * (i % 3 - 1) for i in range(20))
        detector = StatisticalDetector(
            min_data_points=10,
            significance_level=0.05,
            sample_source=_FakeSampleSource(
                baseline=_samples(baseline_samples),
                current=_samples(current_samples),
            ),
        )
        result = await detector.check(
            baseline=baseline,
            current=current,
            thresholds=_thresholds(),
        )
        assert result.verdict == RegressionVerdict.STATISTICAL_REGRESSION
        assert result.p_value is not None
        assert result.p_value < 0.05
        assert result.breached_metric == "quality"

    async def test_insufficient_samples_returns_insufficient_data(self) -> None:
        baseline = _snap(quality=8.0)
        current = _snap(quality=5.0)
        detector = StatisticalDetector(
            min_data_points=10,
            significance_level=0.05,
            sample_source=_FakeSampleSource(
                baseline=_samples((8.0, 8.1)),
                current=_samples((5.0, 5.1)),
            ),
        )
        result = await detector.check(
            baseline=baseline,
            current=current,
            thresholds=_thresholds(),
        )
        assert result.verdict == RegressionVerdict.INSUFFICIENT_DATA

    def test_invalid_min_data_points(self) -> None:
        with pytest.raises(ValueError, match="min_data_points"):
            StatisticalDetector(min_data_points=1)

    def test_invalid_significance_level(self) -> None:
        with pytest.raises(ValueError, match="significance_level"):
            StatisticalDetector(significance_level=1.5)

    def test_satisfies_source_protocol(self) -> None:
        source = _FakeSampleSource(
            baseline=WindowSamples(),
            current=WindowSamples(),
        )
        assert isinstance(source, StatisticalSampleSource)


# ── TieredRegressionDetector ──────────────────────────────────────


class TestTieredRegressionDetector:
    """Tiered detector tests."""

    async def test_threshold_takes_precedence(self) -> None:
        detector = TieredRegressionDetector()
        result = await detector.check(
            baseline=_snap(quality=8.0),
            current=_snap(quality=5.0),
            thresholds=RegressionThresholds(quality_drop=0.10),
        )
        assert result.verdict == RegressionVerdict.THRESHOLD_BREACH

    async def test_statistical_fires_when_threshold_ok(self) -> None:
        baseline = _snap(quality=8.0)
        current = _snap(quality=6.5)
        baseline_samples = tuple(8.0 + 0.1 * (i % 3 - 1) for i in range(20))
        current_samples = tuple(6.5 + 0.1 * (i % 3 - 1) for i in range(20))
        detector = TieredRegressionDetector(
            statistical_detector=StatisticalDetector(
                min_data_points=10,
                significance_level=0.05,
                sample_source=_FakeSampleSource(
                    baseline=_samples(baseline_samples),
                    current=_samples(current_samples),
                ),
            ),
        )
        result = await detector.check(
            baseline=baseline,
            current=current,
            thresholds=RegressionThresholds(quality_drop=0.50),
        )
        assert result.verdict == RegressionVerdict.STATISTICAL_REGRESSION

    async def test_no_regression(self) -> None:
        detector = TieredRegressionDetector()
        result = await detector.check(
            baseline=_snap(quality=7.5),
            current=_snap(quality=7.4),
            thresholds=_thresholds(),
        )
        assert result.verdict == RegressionVerdict.NO_REGRESSION


# ── RollbackExecutor ──────────────────────────────────────────────


class TestRollbackExecutor:
    """Rollback executor tests."""

    async def test_executes_plan(self) -> None:
        from synthorg.core.types import NotBlankStr
        from synthorg.meta.rollout.inverse_dispatch import RollbackHandler

        class _SpyHandler:
            def __init__(self) -> None:
                self.calls: list[RollbackOperation] = []

            async def revert(self, operation: RollbackOperation) -> int:
                self.calls.append(operation)
                return 1

        handler: RollbackHandler = _SpyHandler()
        executor = RollbackExecutor(
            handlers={NotBlankStr("revert_config"): handler},
        )
        result = await executor.execute(_proposal())
        assert result.success
        assert result.changes_applied == 1


# ── BeforeAfterRollout ────────────────────────────────────────────


class _StubApplier:
    @property
    def altitude(self) -> ProposalAltitude:
        return ProposalAltitude.CONFIG_TUNING

    async def apply(self, proposal: ImprovementProposal) -> ApplyResult:
        return ApplyResult(
            success=True,
            changes_applied=len(proposal.config_changes),
        )

    async def dry_run(self, proposal: ImprovementProposal) -> ApplyResult:
        _ = proposal
        return ApplyResult(success=True, changes_applied=0)


class _FailApplier:
    @property
    def altitude(self) -> ProposalAltitude:
        return ProposalAltitude.CONFIG_TUNING

    async def apply(self, proposal: ImprovementProposal) -> ApplyResult:
        _ = proposal
        return ApplyResult(
            success=False,
            error_message="apply failed",
            changes_applied=0,
        )

    async def dry_run(self, proposal: ImprovementProposal) -> ApplyResult:
        _ = proposal
        return ApplyResult(success=True, changes_applied=0)


class _StubDetector:
    @property
    def name(self) -> str:
        return "stub"

    async def check(
        self,
        *,
        baseline: OrgSignalSnapshot,
        current: OrgSignalSnapshot,
        thresholds: RegressionThresholds,
    ) -> RegressionResult:
        _ = baseline, current, thresholds
        return RegressionResult(
            verdict=RegressionVerdict.NO_REGRESSION,
        )


class TestBeforeAfterRollout:
    """Before/after rollout tests."""

    async def test_successful_rollout(self) -> None:
        rollout = BeforeAfterRollout(
            clock=FakeClock(),
            snapshot_builder=_async_snapshot_builder,
            check_interval_hours=4.0,
        )
        result = await rollout.execute(
            proposal=_proposal(),
            applier=_StubApplier(),
            detector=_StubDetector(),
        )
        assert result.outcome == RolloutOutcome.SUCCESS
        assert result.observation_hours_elapsed == 48.0

    async def test_failed_apply(self) -> None:
        rollout = BeforeAfterRollout(
            clock=FakeClock(),
            snapshot_builder=_async_snapshot_builder,
            check_interval_hours=4.0,
        )
        result = await rollout.execute(
            proposal=_proposal(),
            applier=_FailApplier(),
            detector=_StubDetector(),
        )
        assert result.outcome == RolloutOutcome.FAILED


# ── CanarySubsetRollout ───────────────────────────────────────────


class TestCanarySubsetRollout:
    """Canary rollout tests."""

    async def test_successful_canary(self) -> None:
        rollout = CanarySubsetRollout(
            canary_fraction=0.2,
            clock=FakeClock(),
            snapshot_builder=_async_snapshot_builder,
            check_interval_hours=4.0,
        )
        result = await rollout.execute(
            proposal=_proposal(),
            applier=_StubApplier(),
            detector=_StubDetector(),
        )
        assert result.outcome == RolloutOutcome.SUCCESS

    async def test_failed_canary_apply(self) -> None:
        rollout = CanarySubsetRollout(
            clock=FakeClock(),
            snapshot_builder=_async_snapshot_builder,
            check_interval_hours=4.0,
        )
        result = await rollout.execute(
            proposal=_proposal(),
            applier=_FailApplier(),
            detector=_StubDetector(),
        )
        assert result.outcome == RolloutOutcome.FAILED
