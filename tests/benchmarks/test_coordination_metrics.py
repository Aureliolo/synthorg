"""CodSpeed benchmarks for coordination-metrics compute helpers.

All targets are public, synchronous functions in
:mod:`synthorg.budget.coordination_metrics`. Each is called per
metrics rollup (post-task evaluation, dashboard refresh).
"""

import pytest
from pytest_codspeed import BenchmarkFixture

from synthorg.budget.coordination_metrics import (
    compute_amdahl_ceiling,
    compute_efficiency,
    compute_message_overhead,
    compute_redundancy_rate,
    compute_straggler_gap,
    compute_token_speedup_ratio,
)


@pytest.mark.benchmark
def test_compute_efficiency(benchmark: BenchmarkFixture) -> None:
    """Coordination efficiency metric (success rate * SAS/MAS turns ratio)."""

    @benchmark
    def _() -> None:
        compute_efficiency(success_rate=0.85, turns_mas=12.0, turns_sas=8.0)


@pytest.mark.benchmark
def test_compute_amdahl_ceiling(benchmark: BenchmarkFixture) -> None:
    """Amdahl's Law ceiling + recommended team size."""

    @benchmark
    def _() -> None:
        compute_amdahl_ceiling(parallelizable_fraction=0.8)


@pytest.mark.benchmark
def test_compute_straggler_gap_20_agents(benchmark: BenchmarkFixture) -> None:
    """Straggler gap (max - mean) across 20 agents."""
    durations = [(f"agent-{i}", 10.0 + i * 2.5) for i in range(20)]

    @benchmark
    def _() -> None:
        compute_straggler_gap(agent_durations=durations)


@pytest.mark.benchmark
def test_compute_redundancy_rate_100(benchmark: BenchmarkFixture) -> None:
    """Redundancy rate (mean similarity) across 100 samples."""
    similarities = [0.1 + (i % 9) * 0.1 for i in range(100)]

    @benchmark
    def _() -> None:
        compute_redundancy_rate(similarities=similarities)


@pytest.mark.benchmark
def test_compute_token_speedup_ratio(benchmark: BenchmarkFixture) -> None:
    """Token-efficiency / speedup ratio alert check."""

    @benchmark
    def _() -> None:
        compute_token_speedup_ratio(
            tokens_mas=50000.0,
            tokens_sas=20000.0,
            duration_mas=30.0,
            duration_sas=60.0,
        )


@pytest.mark.benchmark
def test_compute_message_overhead(benchmark: BenchmarkFixture) -> None:
    """O(n^2) message-overhead detection from team-size + message count."""

    @benchmark
    def _() -> None:
        compute_message_overhead(
            team_size=10,
            message_count=75,
            quadratic_threshold=0.5,
        )
