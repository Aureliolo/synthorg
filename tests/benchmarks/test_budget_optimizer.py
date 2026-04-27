"""CodSpeed benchmarks for budget optimizer compute hot paths.

Note on import surface: the public
``CostOptimizer.detect_anomalies`` / ``analyze_efficiency`` /
``recommend_downgrades`` API is async, and benching it would let
event-loop overhead dominate per-iteration cost. We bench the
synchronous compute helpers in
:mod:`synthorg.budget._optimizer_helpers` directly because they are
the actual hot paths that the async public methods delegate to. A
refactor of these helpers will require updating bench bodies in
this file -- an accepted tradeoff documented in the perf-system PR
design (see also ``test_budget_aggregation.py`` for the same
pattern).
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from pytest_codspeed import BenchmarkFixture

from synthorg.budget._optimizer_helpers import (
    _classify_severity,
    _compute_window_costs,
    _rate_efficiency,
)
from synthorg.budget.cost_record import CostRecord


@pytest.mark.benchmark
def test_compute_window_costs_12(
    benchmark: BenchmarkFixture,
    cost_records_500: Sequence[CostRecord],
) -> None:
    """Compute per-window costs across 12 daily windows for one agent."""
    agent_records = [r for r in cost_records_500 if r.agent_id == "agent-0"]
    window_duration = timedelta(hours=24)
    window_starts = tuple(
        datetime(2025, 12, 20, tzinfo=UTC) + timedelta(days=d) for d in range(12)
    )

    @benchmark
    def _() -> None:
        _compute_window_costs(agent_records, window_starts, window_duration)


@pytest.mark.benchmark
def test_classify_severity(benchmark: BenchmarkFixture) -> None:
    """Severity classification across the full anomaly z-score range."""
    values = [0.5, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]

    @benchmark
    def _() -> None:
        for v in values:
            _classify_severity(v)


@pytest.mark.benchmark
def test_rate_efficiency(benchmark: BenchmarkFixture) -> None:
    """Efficiency rating relative to a global average."""
    cases = [
        (0.05, 0.10, 1.5, 0.5),  # efficient
        (0.10, 0.10, 1.5, 0.5),  # normal
        (0.20, 0.10, 1.5, 0.5),  # inefficient
        (0.00, 0.00, 1.5, 0.5),  # zero avg
    ]

    @benchmark
    def _() -> None:
        for cost, avg, thresh, lower in cases:
            _rate_efficiency(cost, avg, thresh, lower)
