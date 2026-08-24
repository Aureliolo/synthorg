"""Unit tests for custom declarative signal rules."""

import operator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from synthorg.meta.models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    ProposalAltitude,
    RuleSeverity,
)
from synthorg.meta.rules.custom import (
    METRIC_REGISTRY,
    Comparator,
    CustomRuleDefinition,
    DeclarativeRule,
    MetricDescriptor,
    resolve_metric,
)

pytestmark = pytest.mark.unit


# ── Helpers ────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _full_snapshot(  # noqa: PLR0913
    *,
    quality: float = 7.5,
    success: float = 0.85,
    agents: int = 10,
    spend: float = 150.0,
    coord_ratio: float = 0.3,
    days_left: int | None = 30,
    overhead_pct: float | None = 20.0,
    straggler: float | None = 1.5,
    redundancy: float | None = 0.1,
    coordination_efficiency: float | None = 0.8,
    error_amplification: float | None = 1.2,
    message_density: float | None = 3.0,
    error_findings: int = 3,
    evolution_proposals: int = 2,
    approval_rate: float = 0.5,
    event_count: int = 100,
    error_event_count: int = 5,
) -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=quality,
            avg_success_rate=success,
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
            coordination_efficiency=coordination_efficiency,
            coordination_overhead_pct=overhead_pct,
            error_amplification=error_amplification,
            message_density=message_density,
            straggler_gap_ratio=straggler,
            redundancy_rate=redundancy,
        ),
        errors=OrgErrorSummary(total_findings=error_findings),
        evolution=OrgEvolutionSummary(
            total_proposals=evolution_proposals,
            approval_rate=approval_rate,
        ),
        telemetry=OrgTelemetrySummary(
            event_count=event_count,
            error_event_count=error_event_count,
        ),
    )


def _make_definition(
    *,
    name: str = "test-rule",
    description: str = "A test rule",
    metric_path: str = "performance.avg_quality_score",
    comparator: Comparator = Comparator.LT,
    threshold: float = 5.0,
    severity: RuleSeverity = RuleSeverity.WARNING,
    target_altitudes: tuple[ProposalAltitude, ...] = (ProposalAltitude.CONFIG_TUNING,),
    enabled: bool = True,
) -> CustomRuleDefinition:
    now = _now()
    return CustomRuleDefinition(
        id=uuid4(),
        name=name,
        description=description,
        metric_path=metric_path,
        comparator=comparator,
        threshold=threshold,
        severity=severity,
        target_altitudes=target_altitudes,
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


# ── Comparator enum ───────────────────────────────────────────────


class TestComparator:
    """Tests for the Comparator enum."""

    def test_values(self) -> None:
        assert Comparator.LT.value == "lt"
        assert Comparator.LE.value == "le"
        assert Comparator.GT.value == "gt"
        assert Comparator.GE.value == "ge"
        assert Comparator.EQ.value == "eq"
        assert Comparator.NE.value == "ne"

    def test_all_members(self) -> None:
        assert len(Comparator) == 6

    @pytest.mark.parametrize(
        ("comp", "op"),
        [
            (Comparator.LT, operator.lt),
            (Comparator.LE, operator.le),
            (Comparator.GT, operator.gt),
            (Comparator.GE, operator.ge),
            (Comparator.EQ, operator.eq),
            (Comparator.NE, operator.ne),
        ],
    )
    def test_to_operator(self, comp: Comparator, op: object) -> None:
        assert comp.to_operator() is op


# ── MetricDescriptor ──────────────────────────────────────────────


class TestMetricDescriptor:
    """Tests for MetricDescriptor model."""

    def test_valid(self) -> None:
        md = MetricDescriptor(
            path="performance.avg_quality_score",
            label="Average Quality Score",
            domain="performance",
            value_type="float",
            min_value=0.0,
            max_value=10.0,
            unit=None,
            nullable=False,
        )
        assert md.path == "performance.avg_quality_score"
        assert md.value_type == "float"

    def test_frozen(self) -> None:
        md = MetricDescriptor(
            path="budget.total_spend",
            label="Total Spend",
            domain="budget",
            value_type="float",
            min_value=0.0,
            max_value=None,
            unit="USD",
            nullable=False,
        )
        with pytest.raises(ValidationError, match="frozen"):
            md.path = "other"  # type: ignore[misc]


# ── CustomRuleDefinition ──────────────────────────────────────────


class TestCustomRuleDefinition:
    """Tests for CustomRuleDefinition model."""

    def test_valid(self) -> None:
        defn = _make_definition()
        assert defn.name == "test-rule"
        assert defn.comparator == Comparator.LT

    def test_invalid_metric_path(self) -> None:
        with pytest.raises(ValueError, match="metric_path"):
            _make_definition(metric_path="nonexistent.field")

    def test_requires_at_least_one_altitude(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            _make_definition(target_altitudes=())

    def test_frozen(self) -> None:
        defn = _make_definition()
        with pytest.raises(ValidationError, match="frozen"):
            defn.name = "changed"  # type: ignore[misc]


# ── resolve_metric ────────────────────────────────────────────────


class TestResolveMetric:
    """Tests for the resolve_metric utility."""

    def test_performance_avg_quality_score(self) -> None:
        snap = _full_snapshot(quality=8.0)
        assert resolve_metric(snap, "performance.avg_quality_score") == 8.0

    def test_budget_days_until_exhausted(self) -> None:
        snap = _full_snapshot(days_left=14)
        assert resolve_metric(snap, "budget.days_until_exhausted") == 14

    def test_nullable_returns_none(self) -> None:
        snap = _full_snapshot(overhead_pct=None)
        assert resolve_metric(snap, "coordination.coordination_overhead_pct") is None

    def test_all_registry_paths_resolve(self) -> None:
        snap = _full_snapshot()
        for metric in METRIC_REGISTRY:
            value = resolve_metric(snap, metric.path)
            if metric.nullable:
                assert value is None or isinstance(value, int | float)
            else:
                assert isinstance(value, int | float)

    def test_invalid_path_raises(self) -> None:
        snap = _full_snapshot()
        with pytest.raises(AttributeError):
            resolve_metric(snap, "nonexistent.field")


# ── METRIC_REGISTRY ───────────────────────────────────────────────


class TestMetricRegistry:
    """Tests for the METRIC_REGISTRY completeness."""

    def test_registry_has_expected_count(self) -> None:
        assert len(METRIC_REGISTRY) == 22

    def test_all_paths_unique(self) -> None:
        paths = [m.path for m in METRIC_REGISTRY]
        assert len(paths) == len(set(paths))

    def test_domains_covered(self) -> None:
        domains = {m.domain for m in METRIC_REGISTRY}
        assert domains == {
            "performance",
            "budget",
            "coordination",
            "errors",
            "evolution",
            "telemetry",
        }

    def test_nullable_metrics_match_snapshot(self) -> None:
        nullable_paths = {m.path for m in METRIC_REGISTRY if m.nullable}
        expected_nullable = {
            "budget.days_until_exhausted",
            "coordination.coordination_efficiency",
            "coordination.coordination_overhead_pct",
            "coordination.error_amplification",
            "coordination.message_density",
            "coordination.redundancy_rate",
            "coordination.straggler_gap_ratio",
        }
        assert nullable_paths == expected_nullable


# ── DeclarativeRule ───────────────────────────────────────────────


class TestDeclarativeRule:
    """Tests for the DeclarativeRule class."""

    def test_implements_signal_rule_protocol(self) -> None:
        from synthorg.meta.protocol import SignalRule

        defn = _make_definition()
        rule = DeclarativeRule(defn)
        assert isinstance(rule, SignalRule)

    def test_name_property(self) -> None:
        defn = _make_definition(name="my-custom-rule")
        rule = DeclarativeRule(defn)
        assert rule.name == "my-custom-rule"

    def test_target_altitudes_property(self) -> None:
        altitudes = (
            ProposalAltitude.CONFIG_TUNING,
            ProposalAltitude.ARCHITECTURE,
        )
        defn = _make_definition(target_altitudes=altitudes)
        rule = DeclarativeRule(defn)
        assert rule.target_altitudes == altitudes

    @pytest.mark.parametrize(
        ("comparator", "threshold", "metric_value", "should_fire"),
        [
            (Comparator.LT, 5.0, 3.0, True),
            (Comparator.LT, 5.0, 5.0, False),
            (Comparator.LT, 5.0, 7.0, False),
            (Comparator.LE, 5.0, 5.0, True),
            (Comparator.LE, 5.0, 3.0, True),
            (Comparator.LE, 5.0, 7.0, False),
            (Comparator.GT, 5.0, 7.0, True),
            (Comparator.GT, 5.0, 5.0, False),
            (Comparator.GT, 5.0, 3.0, False),
            (Comparator.GE, 5.0, 5.0, True),
            (Comparator.GE, 5.0, 7.0, True),
            (Comparator.GE, 5.0, 3.0, False),
            (Comparator.EQ, 5.0, 5.0, True),
            (Comparator.EQ, 5.0, 3.0, False),
            (Comparator.NE, 5.0, 3.0, True),
            (Comparator.NE, 5.0, 5.0, False),
        ],
    )
    def test_evaluate_comparators(
        self,
        comparator: Comparator,
        threshold: float,
        metric_value: float,
        *,
        should_fire: bool,
    ) -> None:
        defn = _make_definition(
            metric_path="performance.avg_quality_score",
            comparator=comparator,
            threshold=threshold,
            severity=RuleSeverity.WARNING,
        )
        rule = DeclarativeRule(defn)
        snap = _full_snapshot(quality=metric_value)
        match = rule.evaluate(snap)
        if should_fire:
            assert match is not None
            assert match.rule_name == defn.name
            assert match.severity == RuleSeverity.WARNING
            assert "threshold" in match.signal_context
        else:
            assert match is None

    def test_evaluate_nullable_metric_none_returns_none(self) -> None:
        defn = _make_definition(
            metric_path="coordination.coordination_overhead_pct",
            comparator=Comparator.GT,
            threshold=30.0,
        )
        rule = DeclarativeRule(defn)
        snap = _full_snapshot(overhead_pct=None)
        assert rule.evaluate(snap) is None

    def test_evaluate_nullable_metric_with_value(self) -> None:
        defn = _make_definition(
            metric_path="coordination.coordination_overhead_pct",
            comparator=Comparator.GT,
            threshold=30.0,
        )
        rule = DeclarativeRule(defn)
        snap = _full_snapshot(overhead_pct=40.0)
        match = rule.evaluate(snap)
        assert match is not None
        assert match.severity == RuleSeverity.WARNING

    def test_evaluate_integer_metric(self) -> None:
        defn = _make_definition(
            metric_path="errors.total_findings",
            comparator=Comparator.GT,
            threshold=10.0,
            severity=RuleSeverity.CRITICAL,
        )
        rule = DeclarativeRule(defn)
        snap = _full_snapshot(error_findings=15)
        match = rule.evaluate(snap)
        assert match is not None
        assert match.severity == RuleSeverity.CRITICAL

    def test_evaluate_match_contains_context(self) -> None:
        defn = _make_definition(
            metric_path="performance.avg_quality_score",
            comparator=Comparator.LT,
            threshold=8.0,
        )
        rule = DeclarativeRule(defn)
        snap = _full_snapshot(quality=6.0)
        match = rule.evaluate(snap)
        assert match is not None
        assert match.signal_context["metric_path"] == "performance.avg_quality_score"
        assert match.signal_context["metric_value"] == 6.0
        assert match.signal_context["threshold"] == 8.0
        assert match.signal_context["comparator"] == "lt"

    def test_evaluate_match_suggested_altitudes(self) -> None:
        altitudes = (
            ProposalAltitude.CONFIG_TUNING,
            ProposalAltitude.PROMPT_TUNING,
        )
        defn = _make_definition(
            metric_path="performance.avg_success_rate",
            comparator=Comparator.LT,
            threshold=0.9,
            target_altitudes=altitudes,
        )
        rule = DeclarativeRule(defn)
        snap = _full_snapshot(success=0.5)
        match = rule.evaluate(snap)
        assert match is not None
        assert match.suggested_altitudes == altitudes

    def test_evaluate_budget_metric(self) -> None:
        defn = _make_definition(
            metric_path="budget.days_until_exhausted",
            comparator=Comparator.LE,
            threshold=7.0,
            severity=RuleSeverity.CRITICAL,
        )
        rule = DeclarativeRule(defn)
        snap = _full_snapshot(days_left=5)
        match = rule.evaluate(snap)
        assert match is not None
        assert match.severity == RuleSeverity.CRITICAL

    def test_evaluate_telemetry_metric(self) -> None:
        defn = _make_definition(
            metric_path="telemetry.error_event_count",
            comparator=Comparator.GT,
            threshold=10.0,
        )
        rule = DeclarativeRule(defn)
        snap = _full_snapshot(error_event_count=15)
        match = rule.evaluate(snap)
        assert match is not None
