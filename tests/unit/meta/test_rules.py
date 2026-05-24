"""Unit tests for meta-loop rule engine and built-in rules."""

import pytest

from synthorg.meta.models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    ProposalAltitude,
    RuleSeverity,
)
from synthorg.meta.rules.builtin import (
    BudgetOverrunRule,
    CoordinationCostRatioRule,
    CoordinationOverheadRule,
    ErrorSpikeRule,
    QualityDecliningRule,
    RedundancyRule,
    ScalingFailureRule,
    StragglerBottleneckRule,
    SuccessRateDropRule,
    default_rules,
)
from synthorg.meta.rules.engine import RuleEngine

pytestmark = pytest.mark.unit


# ── Helpers ────────────────────────────────────────────────────────


def _snap(  # noqa: PLR0913
    *,
    quality: float = 7.5,
    success: float = 0.85,
    collab: float = 6.0,
    agents: int = 10,
    spend: float = 150.0,
    coord_ratio: float = 0.3,
    days_left: int | None = None,
    overhead_pct: float | None = None,
    straggler: float | None = None,
    redundancy: float | None = None,
    scaling_total: int = 0,
    scaling_success: float = 0.0,
    error_findings: int = 0,
) -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=quality,
            avg_success_rate=success,
            avg_collaboration_score=collab,
            agent_count=agents,
        ),
        budget=OrgBudgetSummary(
            total_spend=spend,
            productive_ratio=0.6,
            coordination_ratio=coord_ratio,
            system_ratio=0.1,
            days_until_exhausted=days_left,
            forecast_confidence=0.8,
            orchestration_overhead=0.5,
        ),
        coordination=OrgCoordinationSummary(
            coordination_overhead_pct=overhead_pct,
            straggler_gap_ratio=straggler,
            redundancy_rate=redundancy,
        ),
        scaling=OrgScalingSummary(
            total_decisions=scaling_total,
            success_rate=scaling_success,
        ),
        errors=OrgErrorSummary(total_findings=error_findings),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


# ── QualityDecliningRule ───────────────────────────────────────────


class TestQualityDecliningRule:
    """Quality declining rule tests."""

    def test_fires_when_below_threshold(self) -> None:
        rule = QualityDecliningRule(threshold=5.0)
        match = rule.evaluate(_snap(quality=4.5))
        assert match is not None
        assert match.rule_name == "quality_declining"
        assert match.severity == RuleSeverity.WARNING
        assert ProposalAltitude.CONFIG_TUNING in match.suggested_altitudes

    def test_does_not_fire_above_threshold(self) -> None:
        rule = QualityDecliningRule(threshold=5.0)
        assert rule.evaluate(_snap(quality=5.5)) is None

    def test_does_not_fire_at_threshold(self) -> None:
        rule = QualityDecliningRule(threshold=5.0)
        assert rule.evaluate(_snap(quality=5.0)) is None

    def test_does_not_fire_empty_org(self) -> None:
        rule = QualityDecliningRule()
        assert rule.evaluate(_snap(agents=0)) is None

    def test_custom_threshold(self) -> None:
        rule = QualityDecliningRule(threshold=8.0)
        match = rule.evaluate(_snap(quality=7.5))
        assert match is not None


# ── SuccessRateDropRule ────────────────────────────────────────────


class TestSuccessRateDropRule:
    """Success rate drop rule tests."""

    def test_fires_below_threshold(self) -> None:
        rule = SuccessRateDropRule(threshold=0.7)
        match = rule.evaluate(_snap(success=0.65))
        assert match is not None
        assert match.rule_name == "success_rate_drop"

    def test_does_not_fire_above(self) -> None:
        rule = SuccessRateDropRule(threshold=0.7)
        assert rule.evaluate(_snap(success=0.75)) is None

    def test_empty_org(self) -> None:
        rule = SuccessRateDropRule()
        assert rule.evaluate(_snap(agents=0)) is None


# ── BudgetOverrunRule ──────────────────────────────────────────────


class TestBudgetOverrunRule:
    """Budget overrun rule tests."""

    def test_fires_when_days_below_threshold(self) -> None:
        rule = BudgetOverrunRule(days_threshold=14)
        match = rule.evaluate(_snap(days_left=10))
        assert match is not None
        assert match.severity == RuleSeverity.CRITICAL
        assert match.rule_name == "budget_overrun"

    def test_fires_at_threshold(self) -> None:
        rule = BudgetOverrunRule(days_threshold=14)
        match = rule.evaluate(_snap(days_left=14))
        assert match is not None

    def test_does_not_fire_above(self) -> None:
        rule = BudgetOverrunRule(days_threshold=14)
        assert rule.evaluate(_snap(days_left=30)) is None

    def test_does_not_fire_no_forecast(self) -> None:
        rule = BudgetOverrunRule()
        assert rule.evaluate(_snap(days_left=None)) is None


# ── CoordinationCostRatioRule ──────────────────────────────────────


class TestCoordinationCostRatioRule:
    """Coordination cost ratio rule tests."""

    def test_fires_above_threshold(self) -> None:
        rule = CoordinationCostRatioRule(threshold=0.4)
        match = rule.evaluate(_snap(coord_ratio=0.45))
        assert match is not None

    def test_does_not_fire_below(self) -> None:
        rule = CoordinationCostRatioRule(threshold=0.4)
        assert rule.evaluate(_snap(coord_ratio=0.3)) is None


# ── CoordinationOverheadRule ───────────────────────────────────────


class TestCoordinationOverheadRule:
    """Coordination overhead rule tests."""

    def test_fires_above_threshold(self) -> None:
        rule = CoordinationOverheadRule(threshold=35.0)
        match = rule.evaluate(_snap(overhead_pct=40.0))
        assert match is not None

    def test_does_not_fire_below(self) -> None:
        rule = CoordinationOverheadRule(threshold=35.0)
        assert rule.evaluate(_snap(overhead_pct=30.0)) is None

    def test_does_not_fire_none(self) -> None:
        rule = CoordinationOverheadRule()
        assert rule.evaluate(_snap(overhead_pct=None)) is None


# ── StragglerBottleneckRule ────────────────────────────────────────


class TestStragglerBottleneckRule:
    """Straggler bottleneck rule tests."""

    def test_fires_above_threshold(self) -> None:
        rule = StragglerBottleneckRule(threshold=2.0)
        match = rule.evaluate(_snap(straggler=2.5))
        assert match is not None

    def test_does_not_fire_below(self) -> None:
        rule = StragglerBottleneckRule(threshold=2.0)
        assert rule.evaluate(_snap(straggler=1.5)) is None


# ── RedundancyRule ─────────────────────────────────────────────────


class TestRedundancyRule:
    """Redundancy rule tests."""

    def test_fires_above_threshold(self) -> None:
        rule = RedundancyRule(threshold=0.3)
        match = rule.evaluate(_snap(redundancy=0.35))
        assert match is not None

    def test_does_not_fire_below(self) -> None:
        rule = RedundancyRule(threshold=0.3)
        assert rule.evaluate(_snap(redundancy=0.2)) is None


# ── ScalingFailureRule ─────────────────────────────────────────────


class TestScalingFailureRule:
    """Scaling failure rule tests."""

    def test_fires_high_failure_rate(self) -> None:
        rule = ScalingFailureRule(threshold=0.5, min_decisions=3)
        match = rule.evaluate(_snap(scaling_total=5, scaling_success=0.4))
        assert match is not None

    def test_does_not_fire_low_failure(self) -> None:
        rule = ScalingFailureRule(threshold=0.5)
        assert rule.evaluate(_snap(scaling_total=5, scaling_success=0.8)) is None

    def test_does_not_fire_insufficient_decisions(self) -> None:
        rule = ScalingFailureRule(min_decisions=3)
        assert rule.evaluate(_snap(scaling_total=2, scaling_success=0.0)) is None


# ── ErrorSpikeRule ─────────────────────────────────────────────────


class TestErrorSpikeRule:
    """Error spike rule tests."""

    def test_fires_above_threshold(self) -> None:
        rule = ErrorSpikeRule(threshold=10)
        match = rule.evaluate(_snap(error_findings=15))
        assert match is not None

    def test_does_not_fire_below(self) -> None:
        rule = ErrorSpikeRule(threshold=10)
        assert rule.evaluate(_snap(error_findings=5)) is None


# ── default_rules ──────────────────────────────────────────────────


class TestDefaultRules:
    """Default rules set tests."""

    def test_returns_9_rules(self) -> None:
        rules = default_rules()
        assert len(rules) == 9

    def test_all_have_unique_names(self) -> None:
        rules = default_rules()
        names = [r.name for r in rules]
        assert len(names) == len(set(names))


# ── RuleEngine ─────────────────────────────────────────────────────


class TestRuleEngine:
    """Rule engine tests."""

    def test_no_rules_no_matches(self) -> None:
        engine = RuleEngine(rules=())
        matches = engine.evaluate(_snap())
        assert matches == ()

    def test_single_rule_fires(self) -> None:
        engine = RuleEngine(rules=(QualityDecliningRule(threshold=8.0),))
        matches = engine.evaluate(_snap(quality=7.5))
        assert len(matches) == 1
        assert matches[0].rule_name == "quality_declining"

    def test_single_rule_no_match(self) -> None:
        engine = RuleEngine(rules=(QualityDecliningRule(threshold=5.0),))
        matches = engine.evaluate(_snap(quality=7.5))
        assert matches == ()

    def test_multiple_rules_some_fire(self) -> None:
        engine = RuleEngine(
            rules=(
                QualityDecliningRule(threshold=8.0),
                SuccessRateDropRule(threshold=0.9),
                BudgetOverrunRule(days_threshold=14),
            )
        )
        snap = _snap(quality=7.0, success=0.85, days_left=30)
        matches = engine.evaluate(snap)
        assert len(matches) == 2
        names = {m.rule_name for m in matches}
        assert "quality_declining" in names
        assert "success_rate_drop" in names

    def test_severity_ordering(self) -> None:
        engine = RuleEngine(
            rules=(
                QualityDecliningRule(threshold=8.0),  # WARNING
                BudgetOverrunRule(days_threshold=30),  # CRITICAL
            )
        )
        snap = _snap(quality=7.0, days_left=10)
        matches = engine.evaluate(snap)
        assert len(matches) == 2
        assert matches[0].severity == RuleSeverity.CRITICAL
        assert matches[1].severity == RuleSeverity.WARNING

    def test_rule_count_and_names(self) -> None:
        rules = default_rules()
        engine = RuleEngine(rules=rules)
        assert engine.rule_count == 9
        assert "quality_declining" in engine.rule_names

    def test_exception_in_rule_does_not_crash(self) -> None:
        """A failing rule should not prevent other rules from running."""

        class BrokenRule:
            @property
            def name(self) -> str:
                return "broken"

            @property
            def target_altitudes(
                self,
            ) -> tuple[ProposalAltitude, ...]:
                return (ProposalAltitude.CONFIG_TUNING,)

            def evaluate(self, snapshot: OrgSignalSnapshot) -> None:
                msg = "boom"
                raise RuntimeError(msg)

        engine = RuleEngine(
            rules=(
                BrokenRule(),
                QualityDecliningRule(threshold=8.0),
            )
        )
        matches = engine.evaluate(_snap(quality=7.0))
        assert len(matches) == 1
        assert matches[0].rule_name == "quality_declining"

    @pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
    def test_propagates_catastrophic_interpreter_errors(
        self,
        exc_cls: type[BaseException],
    ) -> None:
        """Catastrophic interpreter errors from a rule must escape the loop.

        Per-rule exceptions are swallowed and logged so a single bad
        rule cannot mask the rest of the rule set, but ``MemoryError``
        and ``RecursionError`` are catastrophic interpreter state and
        MUST propagate so the surrounding orchestrator can fail fast
        instead of silently dropping every subsequent rule.
        """

        class CatastrophicRule:
            @property
            def name(self) -> str:
                return "catastrophic"

            @property
            def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
                return (ProposalAltitude.CONFIG_TUNING,)

            def evaluate(self, snapshot: OrgSignalSnapshot) -> None:
                raise exc_cls

        engine = RuleEngine(rules=(CatastrophicRule(),))
        with pytest.raises(exc_cls):
            engine.evaluate(_snap())
