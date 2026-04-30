"""Early-exit tests for BeforeAfter, Canary, and A/B rollout observation loops."""

from uuid import UUID

import pytest

from synthorg.core.types import NotBlankStr
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
from synthorg.meta.rollout.ab_comparator import ABTestComparator
from synthorg.meta.rollout.ab_test import ABTestRollout
from synthorg.meta.rollout.before_after import BeforeAfterRollout
from synthorg.meta.rollout.group_aggregator import GroupSamples
from tests._shared.fake_clock import FakeClock
from tests.unit.meta.rollout._ramp import ramp as _ramp

pytestmark = pytest.mark.unit


def _snapshot() -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=7.0,
            avg_success_rate=0.8,
            avg_collaboration_score=6.0,
            agent_count=10,
        ),
        budget=OrgBudgetSummary(
            total_spend=100.0,
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


async def _snapshot_builder() -> OrgSignalSnapshot:
    return _snapshot()


# Fixed proposal id keeps the SHA-256 based control/treatment split
# deterministic across runs. The split drives sample tuple lengths,
# which drive Welch's t-statistic; randomizing the id makes mid-window
# regression exit timing flake when the split lands in a pathological
# range.
_FIXED_PROPOSAL_ID = UUID("00000000-0000-4000-8000-000000000001")


def _proposal(window_hours: int = 48) -> ImprovementProposal:
    return ImprovementProposal(
        id=_FIXED_PROPOSAL_ID,
        altitude=ProposalAltitude.CONFIG_TUNING,
        title="test",
        description="test",
        rationale=ProposalRationale(
            signal_summary="s",
            pattern_detected="p",
            expected_impact="e",
            confidence_reasoning="c",
        ),
        config_changes=(
            ConfigChange(path="a.b", old_value=1, new_value=2, description="d"),
        ),
        rollback_plan=RollbackPlan(
            operations=(
                RollbackOperation(
                    operation_type="revert_config",
                    target="a.b",
                    previous_value=1,
                    description="revert",
                ),
            ),
            validation_check="ok",
        ),
        confidence=0.8,
        observation_window_hours=window_hours,
    )


class _OkApplier:
    @property
    def altitude(self) -> ProposalAltitude:
        return ProposalAltitude.CONFIG_TUNING

    async def apply(self, proposal: ImprovementProposal) -> ApplyResult:
        return ApplyResult(success=True, changes_applied=len(proposal.config_changes))

    async def dry_run(self, proposal: ImprovementProposal) -> ApplyResult:
        _ = proposal
        return ApplyResult(success=True, changes_applied=0)


class _BreachingDetector:
    """Returns THRESHOLD_BREACH on the Nth call (1-indexed)."""

    def __init__(self, *, trip_on_call: int = 1) -> None:
        self._trip_on_call = trip_on_call
        self._calls = 0

    @property
    def name(self) -> str:
        return "breaching"

    async def check(
        self,
        *,
        baseline: OrgSignalSnapshot,
        current: OrgSignalSnapshot,
        thresholds: RegressionThresholds,
    ) -> RegressionResult:
        _ = baseline, current, thresholds
        self._calls += 1
        if self._calls >= self._trip_on_call:
            return RegressionResult(
                verdict=RegressionVerdict.THRESHOLD_BREACH,
                breached_metric=NotBlankStr("quality"),
                baseline_value=8.0,
                current_value=6.0,
                threshold=0.10,
            )
        return RegressionResult(verdict=RegressionVerdict.NO_REGRESSION)


class TestBeforeAfterEarlyExit:
    async def test_threshold_breach_mid_window_exits_early(self) -> None:
        rollout = BeforeAfterRollout(
            clock=FakeClock(),
            snapshot_builder=_snapshot_builder,
            check_interval_hours=4.0,
        )
        detector = _BreachingDetector(trip_on_call=2)
        result = await rollout.execute(
            proposal=_proposal(window_hours=48),
            applier=_OkApplier(),
            detector=detector,
        )
        assert result.outcome == RolloutOutcome.REGRESSED
        assert result.regression_verdict == RegressionVerdict.THRESHOLD_BREACH
        # Trip on call #2: 2 ticks * 4 hours = 8.0 hours elapsed
        assert result.observation_hours_elapsed == 8.0
        assert result.details == "quality"

    async def test_first_tick_breach_exits_immediately(self) -> None:
        rollout = BeforeAfterRollout(
            clock=FakeClock(),
            snapshot_builder=_snapshot_builder,
            check_interval_hours=4.0,
        )
        detector = _BreachingDetector(trip_on_call=1)
        result = await rollout.execute(
            proposal=_proposal(window_hours=48),
            applier=_OkApplier(),
            detector=detector,
        )
        assert result.outcome == RolloutOutcome.REGRESSED
        assert result.observation_hours_elapsed == 4.0


class _StaticRoster:
    async def list_agent_ids(self) -> tuple[NotBlankStr, ...]:
        return tuple(NotBlankStr(f"agent-{n}") for n in range(30))


class TestABTestMidWindowRegressionExit:
    async def test_treatment_regressed_mid_window_exits_early(self) -> None:
        # Build an aggregator that returns fixed good samples for control
        # and catastrophic-drop samples for treatment on tick 2+.
        calls = {"n": 0}

        class _AltAgg:
            async def aggregate_for_agents(
                self,
                *,
                agent_ids: tuple[NotBlankStr, ...],
                since: object,
                until: object,
            ) -> GroupSamples:
                _ = since, until
                calls["n"] += 1
                # ab_test.py schedules control then treatment per tick, so
                # odd calls (1, 3, 5...) are control and even calls (2, 4,
                # 6...) are treatment. Pairs index as tick 0, 1, 2... so
                # the first pair (calls 1-2) is tick 0, the second pair
                # (calls 3-4) is tick 1, etc. With a 4h check interval the
                # second pair corresponds to 8h elapsed, which is the
                # assertion below.
                tick = (calls["n"] - 1) // 2
                is_treatment = calls["n"] % 2 == 0
                n = len(agent_ids)
                if is_treatment and tick >= 1:
                    q = _ramp(2.0, 20, 0.3)
                else:
                    q = _ramp(7.5, 20, 0.3)
                return GroupSamples(
                    agent_ids=agent_ids,
                    quality_samples=q[:n] if n <= 20 else q + (q[-1],) * (n - 20),
                    success_samples=_ramp(0.85, n, 0.0),
                    spend_samples=_ramp(1.0, n, 0.0),
                )

        rollout = ABTestRollout(
            control_fraction=0.5,
            min_agents_per_group=5,
            min_observations_per_group=10,
            clock=FakeClock(),
            roster=_StaticRoster(),
            group_aggregator=_AltAgg(),
            comparator=ABTestComparator(min_observations=10),
            check_interval_hours=4.0,
        )

        class _OkDetector:
            @property
            def name(self) -> str:
                return "ok"

            async def check(
                self,
                *,
                baseline: OrgSignalSnapshot,
                current: OrgSignalSnapshot,
                thresholds: RegressionThresholds,
            ) -> RegressionResult:
                _ = baseline, current, thresholds
                return RegressionResult(verdict=RegressionVerdict.NO_REGRESSION)

        result = await rollout.execute(
            proposal=_proposal(window_hours=48),
            applier=_OkApplier(),
            detector=_OkDetector(),
        )
        assert result.outcome == RolloutOutcome.REGRESSED
        assert result.regression_verdict == RegressionVerdict.STATISTICAL_REGRESSION
        # _AltAgg trips on tick >= 1 (0-indexed), i.e. the second call
        # pair. Each pair = 4h, so two ticks = 8.0 hours elapsed.
        assert result.observation_hours_elapsed == 8.0
