"""Unit tests for meta-loop protocol compliance."""

from datetime import UTC, datetime

import pytest

from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.guards.approval_gate import ApprovalGateGuard
from synthorg.meta.guards.rate_limit import RateLimitGuard
from synthorg.meta.guards.rollback_plan import RollbackPlanGuard
from synthorg.meta.guards.scope_check import ScopeCheckGuard
from synthorg.meta.models import (
    ApplyResult,
    GuardResult,
    GuardVerdict,
    ImprovementProposal,
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    ProposalAltitude,
    RegressionResult,
    RegressionThresholds,
    RegressionVerdict,
    RolloutOutcome,
    RolloutResult,
    RuleMatch,
    RuleSeverity,
)
from synthorg.meta.protocol import (
    ImprovementStrategy,
    ProposalApplier,
    ProposalGuard,
    RegressionDetector,
    RolloutStrategy,
    SignalAggregator,
    SignalRule,
)
from synthorg.meta.rollout.ab_test import ABTestRollout
from synthorg.meta.rollout.before_after import BeforeAfterRollout
from synthorg.meta.rollout.canary import CanarySubsetRollout
from synthorg.meta.rollout.regression.composite import TieredRegressionDetector
from synthorg.meta.rollout.regression.statistical import StatisticalDetector
from synthorg.meta.rollout.regression.threshold import ThresholdDetector

pytestmark = pytest.mark.unit


def _make_snapshot() -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=7.5,
            avg_success_rate=0.85,
            agent_count=10,
        ),
        budget=OrgBudgetSummary(
            total_spend=150.0,
            productive_ratio=0.6,
            coordination_ratio=0.3,
            system_ratio=0.1,
            forecast_confidence=0.8,
            orchestration_overhead=0.5,
        ),
        coordination=OrgCoordinationSummary(),
        errors=OrgErrorSummary(),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


# ── Concrete test implementations ─────────────────────────────────


class StubAggregator:
    """Minimal SignalAggregator implementation for testing."""

    @property
    def domain(self) -> str:
        return "test"

    async def aggregate(self, *, since: datetime, until: datetime) -> dict[str, object]:
        return {"test_metric": 42}


class StubRule:
    """Minimal SignalRule implementation for testing."""

    @property
    def name(self) -> str:
        return "test_rule"

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        return (ProposalAltitude.CONFIG_TUNING,)

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        return RuleMatch(
            rule_name="test_rule",
            severity=RuleSeverity.INFO,
            description="test match",
            suggested_altitudes=(ProposalAltitude.CONFIG_TUNING,),
        )


class StubStrategy:
    """Minimal ImprovementStrategy implementation for testing."""

    @property
    def altitude(self) -> ProposalAltitude:
        return ProposalAltitude.CONFIG_TUNING

    async def propose(
        self,
        *,
        snapshot: OrgSignalSnapshot,
        triggered_rules: tuple[RuleMatch, ...],
    ) -> tuple[ImprovementProposal, ...]:
        return ()


class StubGuard:
    """Minimal ProposalGuard implementation for testing."""

    @property
    def name(self) -> str:
        return "test_guard"

    async def evaluate(self, proposal: ImprovementProposal) -> GuardResult:
        return GuardResult(
            guard_name="test_guard",
            verdict=GuardVerdict.PASSED,
        )


class StubApplier:
    """Minimal ProposalApplier implementation for testing."""

    @property
    def altitude(self) -> ProposalAltitude:
        return ProposalAltitude.CONFIG_TUNING

    async def apply(self, proposal: ImprovementProposal) -> ApplyResult:
        return ApplyResult(success=True, changes_applied=1)

    async def dry_run(self, proposal: ImprovementProposal) -> ApplyResult:
        return ApplyResult(success=True, changes_applied=0)


class StubRolloutStrategy:
    """Minimal RolloutStrategy implementation for testing."""

    @property
    def name(self) -> str:
        return "test_rollout"

    async def execute(
        self,
        *,
        proposal: ImprovementProposal,
        applier: ProposalApplier,
        detector: RegressionDetector,
    ) -> RolloutResult:
        return RolloutResult(
            proposal_id=proposal.id,
            outcome=RolloutOutcome.SUCCESS,
            observation_hours_elapsed=48.0,
        )


class StubRegressionDetector:
    """Minimal RegressionDetector implementation for testing."""

    @property
    def name(self) -> str:
        return "test_detector"

    async def check(
        self,
        *,
        baseline: OrgSignalSnapshot,
        current: OrgSignalSnapshot,
        thresholds: RegressionThresholds,
    ) -> RegressionResult:
        return RegressionResult(verdict=RegressionVerdict.NO_REGRESSION)


# ── Protocol compliance tests ──────────────────────────────────────


class TestSignalAggregatorProtocol:
    """SignalAggregator protocol compliance."""

    def test_isinstance_check(self) -> None:
        assert isinstance(StubAggregator(), SignalAggregator)

    async def test_aggregate(self) -> None:
        agg = StubAggregator()
        now = datetime.now(UTC)
        result = await agg.aggregate(since=now, until=now)
        assert result["test_metric"] == 42


class TestSignalRuleProtocol:
    """SignalRule protocol compliance."""

    def test_isinstance_check(self) -> None:
        assert isinstance(StubRule(), SignalRule)

    def test_evaluate_returns_match(self) -> None:
        rule = StubRule()
        match = rule.evaluate(_make_snapshot())
        assert match is not None
        assert match.rule_name == "test_rule"


class TestImprovementStrategyProtocol:
    """ImprovementStrategy protocol compliance."""

    def test_isinstance_check(self) -> None:
        assert isinstance(StubStrategy(), ImprovementStrategy)

    async def test_propose_empty(self) -> None:
        strategy = StubStrategy()
        proposals = await strategy.propose(
            snapshot=_make_snapshot(),
            triggered_rules=(),
        )
        assert proposals == ()


class TestProposalGuardProtocol:
    """ProposalGuard protocol compliance."""

    def test_isinstance_check(self) -> None:
        assert isinstance(StubGuard(), ProposalGuard)


class TestProposalApplierProtocol:
    """ProposalApplier protocol compliance."""

    def test_isinstance_check(self) -> None:
        assert isinstance(StubApplier(), ProposalApplier)


class TestRolloutStrategyProtocol:
    """RolloutStrategy protocol compliance."""

    def test_isinstance_check(self) -> None:
        assert isinstance(StubRolloutStrategy(), RolloutStrategy)


class TestRegressionDetectorProtocol:
    """RegressionDetector protocol compliance."""

    def test_isinstance_check(self) -> None:
        assert isinstance(StubRegressionDetector(), RegressionDetector)


# ── NotBlankStr name compliance ──────────────────────────────────


class TestGuardNameReturnsNotBlankStr:
    """All guards must return NotBlankStr from their name property."""

    @pytest.mark.parametrize(
        "guard",
        [
            ApprovalGateGuard(),
            RateLimitGuard(max_proposals=10, window_hours=24),
            RollbackPlanGuard(),
            ScopeCheckGuard(config=SelfImprovementConfig()),
        ],
        ids=["approval_gate", "rate_limit", "rollback_plan", "scope_check"],
    )
    def test_name_is_not_blank_str(self, guard: ProposalGuard) -> None:
        name = guard.name
        assert isinstance(name, str)
        assert len(name) > 0
        assert name.strip() == name


class TestRolloutStrategyNameReturnsNotBlankStr:
    """All rollout strategies must return NotBlankStr from name."""

    @pytest.mark.parametrize(
        "strategy",
        [
            BeforeAfterRollout(),
            CanarySubsetRollout(),
            ABTestRollout(),
        ],
        ids=["before_after", "canary", "ab_test"],
    )
    def test_name_is_not_blank_str(self, strategy: RolloutStrategy) -> None:
        name = strategy.name
        assert isinstance(name, str)
        assert len(name) > 0
        assert name.strip() == name


class TestRegressionDetectorNameReturnsNotBlankStr:
    """All regression detectors must return NotBlankStr from name."""

    @pytest.mark.parametrize(
        "detector",
        [
            TieredRegressionDetector(),
            ThresholdDetector(),
            StatisticalDetector(),
        ],
        ids=["tiered", "threshold", "statistical"],
    )
    def test_name_is_not_blank_str(self, detector: RegressionDetector) -> None:
        name = detector.name
        assert isinstance(name, str)
        assert len(name) > 0
        assert name.strip() == name
