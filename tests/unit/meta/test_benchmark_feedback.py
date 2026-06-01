# module-kind: tests
"""End-to-end proof that the benchmark signal *drives* a corrective action.

The golden-benchmark quality signal feeds back into the self-improvement
loop so that a measured regression turns into a concrete corrective
proposal.

The chain exercised is the real pipeline -- recorded scorecard history
-> ``BenchmarkSignalAggregator`` -> ``SnapshotBuilder`` -> ``RuleEngine``
with the production ``default_rules()`` -> a CRITICAL ``RuleMatch``
carrying remediation altitudes. The six non-benchmark aggregators are
stubbed to empty so the proof isolates the benchmark feedback path; the
scorecard history files, the benchmark aggregation, the snapshot
assembly, and every rule evaluation are the real code path.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.learning_curve import ScorecardSummary, append_summary
from synthorg.meta.models import (
    ProposalAltitude,
    RuleSeverity,
)
from synthorg.meta.rules.benchmark_rule import BenchmarkRegressionRule
from synthorg.meta.rules.builtin import default_rules
from synthorg.meta.rules.engine import RuleEngine
from synthorg.meta.signal_models import (
    OrgBenchmarkSummary,
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
)
from synthorg.meta.signals.benchmark import BenchmarkSignalAggregator
from synthorg.meta.signals.budget import BudgetSignalAggregator
from synthorg.meta.signals.coordination import CoordinationSignalAggregator
from synthorg.meta.signals.errors import ErrorSignalAggregator
from synthorg.meta.signals.evolution import EvolutionSignalAggregator
from synthorg.meta.signals.performance import PerformanceSignalAggregator
from synthorg.meta.signals.scaling import ScalingSignalAggregator
from synthorg.meta.signals.snapshot import SnapshotBuilder
from synthorg.meta.signals.telemetry import TelemetrySignalAggregator
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_BASE = datetime(2026, 1, 1, tzinfo=UTC)
_MAX_TOTAL = 100

_EMPTY_PERFORMANCE = OrgPerformanceSummary(
    avg_quality_score=0.0,
    avg_success_rate=0.0,
    avg_collaboration_score=0.0,
    agent_count=0,
)
_EMPTY_BUDGET = OrgBudgetSummary(
    total_spend=0.0,
    productive_ratio=0.0,
    coordination_ratio=0.0,
    system_ratio=0.0,
    forecast_confidence=0.0,
    orchestration_overhead=0.0,
)


def _write_history(history_dir: Path, totals: list[int]) -> None:
    for index, total in enumerate(totals):
        append_summary(
            history_dir,
            ScorecardSummary(
                run_label=NotBlankStr(f"run-{index:03d}"),
                generated_at=_BASE + timedelta(hours=index),
                total=total,
                max_total=_MAX_TOTAL,
                is_passing=total >= 60,
            ),
        )


def _builder(history_dir: Path) -> SnapshotBuilder:
    """Real SnapshotBuilder with a real benchmark aggregator.

    The six non-benchmark aggregators are stubbed to empty summaries so
    the assembled snapshot carries only the benchmark signal.
    """
    return SnapshotBuilder(
        performance=mock_of[PerformanceSignalAggregator](
            aggregate=AsyncMock(return_value=_EMPTY_PERFORMANCE),
        ),
        budget=mock_of[BudgetSignalAggregator](
            aggregate=AsyncMock(return_value=_EMPTY_BUDGET),
        ),
        coordination=mock_of[CoordinationSignalAggregator](
            aggregate=AsyncMock(return_value=OrgCoordinationSummary()),
        ),
        scaling=mock_of[ScalingSignalAggregator](
            aggregate=AsyncMock(return_value=OrgScalingSummary()),
        ),
        errors=mock_of[ErrorSignalAggregator](
            aggregate=AsyncMock(return_value=OrgErrorSummary()),
        ),
        evolution=mock_of[EvolutionSignalAggregator](
            aggregate=AsyncMock(return_value=OrgEvolutionSummary()),
        ),
        telemetry=mock_of[TelemetrySignalAggregator](
            aggregate=AsyncMock(return_value=OrgTelemetrySummary()),
        ),
        benchmark=BenchmarkSignalAggregator(history_dir),
    )


async def _build(history_dir: Path) -> object:
    return await _builder(history_dir).build(
        since=_BASE - timedelta(days=7),
        until=_BASE + timedelta(days=1),
    )


async def test_regression_drives_a_critical_corrective_proposal(
    tmp_path: Path,
) -> None:
    """A regressed benchmark curve fires a CRITICAL rule end-to-end."""
    _write_history(tmp_path, [80, 75, 20])

    snapshot = await _build(tmp_path)
    assert snapshot.benchmark.is_regression is True  # type: ignore[attr-defined]
    assert snapshot.benchmark.run_count == 3  # type: ignore[attr-defined]

    matches = RuleEngine(rules=default_rules()).evaluate(snapshot)  # type: ignore[arg-type]

    regression = next(m for m in matches if m.rule_name == "benchmark_regression")
    assert regression.severity is RuleSeverity.CRITICAL
    assert ProposalAltitude.PROMPT_TUNING in regression.suggested_altitudes
    assert ProposalAltitude.CODE_MODIFICATION in regression.suggested_altitudes
    # CRITICAL sorts first, so the corrective signal leads the matches.
    assert matches[0].rule_name == "benchmark_regression"


async def test_rising_curve_drives_no_corrective_proposal(tmp_path: Path) -> None:
    """A healthy, rising curve produces no benchmark regression match."""
    _write_history(tmp_path, [20, 50, 80])

    snapshot = await _build(tmp_path)
    assert snapshot.benchmark.is_regression is False  # type: ignore[attr-defined]

    matches = RuleEngine(rules=default_rules()).evaluate(snapshot)  # type: ignore[arg-type]
    assert not any(m.rule_name == "benchmark_regression" for m in matches)


async def test_aggregator_summarises_latest_run(tmp_path: Path) -> None:
    """The aggregator reports the latest run's score, delta, and flags."""
    _write_history(tmp_path, [40, 70])
    summary = await BenchmarkSignalAggregator(tmp_path).aggregate(
        since=_BASE, until=_BASE + timedelta(days=1)
    )
    assert summary.run_count == 2
    assert summary.latest_total == 70
    assert summary.delta == 30
    assert summary.is_regression is False


async def test_aggregator_empty_without_history() -> None:
    """No configured history directory yields an empty summary, not an error."""
    summary = await BenchmarkSignalAggregator(None).aggregate(
        since=_BASE, until=_BASE + timedelta(days=1)
    )
    assert summary.run_count == 0
    assert summary.is_regression is False


def test_rule_needs_a_predecessor_run() -> None:
    """A single recorded run cannot be a regression (no predecessor)."""
    single = OrgBenchmarkSummary(
        run_count=1, latest_total=10, max_total=100, is_regression=True
    )
    snapshot = OrgSignalSnapshot(
        performance=_EMPTY_PERFORMANCE,
        budget=_EMPTY_BUDGET,
        coordination=OrgCoordinationSummary(),
        scaling=OrgScalingSummary(),
        errors=OrgErrorSummary(),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
        benchmark=single,
    )
    assert BenchmarkRegressionRule().evaluate(snapshot) is None
