"""Shared fixtures for the performance-benchmark suite.

All fixtures are deterministic and ``scope="module"`` so the bench
iteration cost measures only the function under test, not the fixture
build.

The benchmark suite runs under ``pytest --codspeed`` in CI (CPU
Simulation mode via valgrind/cachegrind, deterministic CPU-instruction
counting). Locally, ``--codspeed`` is a no-op and the benches just
execute as regular pytest tests so contributors can sanity-check
fixture wiring.

Builders + constants live in ``tests/benchmarks/_helpers.py`` so test
modules can import them directly.
"""

import logging
from collections.abc import Sequence

import pytest
import structlog

from synthorg.budget.cost_record import CostRecord
from synthorg.memory.models import MemoryEntry
from tests.benchmarks._helpers import make_cost_record, make_memory_entry


@pytest.fixture(autouse=True)
def _silence_logging_for_benchmarks(_reset_structlog_state: None) -> None:
    """Neutralise logging so benches measure compute, not log rendering.

    Hot-path code that logs per call (e.g. ``AgentTaskScorer.score`` emits a
    ``logger.debug`` for every ``(agent, subtask)`` pair) otherwise has its
    benchmark dominated by log rendering + the stdout ``write()`` syscall +
    the malloc churn of building each event, not the function under test.
    That logging I/O is highly environment-sensitive, so it swung the
    routing-scorer bench ~28% across CPU/allocator differences and produced
    spurious CodSpeed regressions.

    The root ``tests/conftest.py`` ``_reset_structlog_state`` fixture resets
    structlog to its defaults before every test (default factory =
    ``PrintLogger`` rendering to stdout, no level gate), so this fixture
    depends on it and re-silences AFTER the reset: a CRITICAL filtering
    wrapper turns every below-critical call into an immediate no-op, leaving
    the bench to measure only the code under test, deterministically across
    runs and hardware.
    """
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
    )


@pytest.fixture(scope="module")
def entries_100() -> tuple[MemoryEntry, ...]:
    """100 personal memory entries with bounded age + relevance variance."""
    return tuple(
        make_memory_entry(i, age_hours=i * 0.5, relevance=0.5 + (i % 10) * 0.05)
        for i in range(100)
    )


@pytest.fixture(scope="module")
def entries_1000() -> tuple[MemoryEntry, ...]:
    """1000 personal memory entries -- exercises the full ranking pipeline at scale."""
    return tuple(
        make_memory_entry(i, age_hours=i * 0.1, relevance=0.4 + (i % 20) * 0.03)
        for i in range(1000)
    )


@pytest.fixture(scope="module")
def shared_entries_50() -> tuple[MemoryEntry, ...]:
    """50 shared (cross-agent) entries used by the merge-path bench."""
    return tuple(
        make_memory_entry(
            5000 + i,
            age_hours=i * 1.0,
            relevance=0.6 + (i % 8) * 0.05,
        )
        for i in range(50)
    )


@pytest.fixture(scope="module")
def cost_records_500() -> Sequence[CostRecord]:
    """500 cost records across 10 agents -- group_by_agent / window benches.

    Returns an immutable ``tuple`` so a test cannot accidentally mutate
    the module-scoped fixture and bleed state into subsequent tests.
    The ``Sequence`` return type keeps the call-site interface flexible
    (callers iterate or index, never mutate); callers that need a
    mutable copy can do ``list(fixture)``.
    """
    agents = [f"agent-{a}" for a in range(10)]
    return tuple(
        make_cost_record(
            i,
            agent_id=agents[i % 10],
            cost=0.01 + (i % 20) * 0.005,
            input_tokens=100 + i * 10,
            output_tokens=50 + i * 5,
            hours_ago=i * 0.5,
        )
        for i in range(500)
    )


@pytest.fixture(scope="module")
def cost_records_2000() -> Sequence[CostRecord]:
    """2000 cost records across 20 agents -- sum_cost / sum_tokens at scale.

    See :func:`cost_records_500` for the rationale on returning a tuple
    (immutable, ``Sequence``-typed for caller flexibility).
    """
    agents = [f"agent-{a}" for a in range(20)]
    return tuple(
        make_cost_record(
            i,
            agent_id=agents[i % 20],
            cost=0.02 + (i % 30) * 0.003,
            input_tokens=200 + i * 5,
            output_tokens=80 + i * 3,
            hours_ago=i * 0.25,
        )
        for i in range(2000)
    )
