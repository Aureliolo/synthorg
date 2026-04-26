"""CodSpeed benchmarks for budget aggregation hot paths.

Note on import surface: the public ``CostTracker`` API
(``get_total_cost``, ``get_agent_cost``, etc.) is async and serialises
on an ``asyncio.Lock``; an ``asyncio.run`` per bench iteration would
let event-loop overhead dominate the CPU-instruction count and obscure
the actual aggregation cost. We therefore bench the synchronous
compute backbone in :mod:`synthorg.budget._aggregation` directly --
these are the functions the async wrappers delegate to. A future
refactor of the aggregation helpers will require updating bodies in
this file; that is an accepted tradeoff documented in the perf-system
PR design.
"""

import pytest
from pytest_codspeed import BenchmarkFixture

from synthorg.budget._aggregation import (
    compute_cost_per_1k,
    group_by_agent,
    sum_cost,
    sum_tokens,
)
from synthorg.budget.cost_record import CostRecord


@pytest.mark.benchmark
def test_group_by_agent_500(
    benchmark: BenchmarkFixture,
    cost_records_500: list[CostRecord],
) -> None:
    """Group 500 cost records by ``agent_id`` (10 agents)."""

    @benchmark
    def _() -> None:
        group_by_agent(cost_records_500)


@pytest.mark.benchmark
def test_sum_cost_2000(
    benchmark: BenchmarkFixture,
    cost_records_2000: list[CostRecord],
) -> None:
    """Sum cost across 2000 records (math.fsum precision path)."""

    @benchmark
    def _() -> None:
        sum_cost(cost_records_2000)


@pytest.mark.benchmark
def test_sum_tokens_2000(
    benchmark: BenchmarkFixture,
    cost_records_2000: list[CostRecord],
) -> None:
    """Sum input + output tokens across 2000 records."""

    @benchmark
    def _() -> None:
        sum_tokens(cost_records_2000)


@pytest.mark.benchmark
def test_compute_cost_per_1k(benchmark: BenchmarkFixture) -> None:
    """Cost-per-1k-tokens derivation (called per metrics rollup)."""

    @benchmark
    def _() -> None:
        compute_cost_per_1k(125.50, 2_500_000)
