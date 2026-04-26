"""tracemalloc-based heap-ceiling tests for Python hot paths.

Complements the CodSpeed CPU-instruction benches: a regression that
doubles peak heap (e.g. accidentally building a list when a generator
would do) is invisible to CodSpeed but very visible to users running
on memory-constrained containers. These tests assert peak-heap
ceilings on a small set of high-fanout paths.

Ceilings were captured 2026-04-26 against ``main`` and carry a small
headroom factor for run-to-run variance. Bump only with explicit user
approval -- the existing ``scripts/check_no_edit_baseline.sh``
PreToolUse hook does not cover this file, but the same convention
applies.
"""

import tracemalloc
from collections.abc import Callable
from typing import Final

import pytest

from synthorg.budget._aggregation import group_by_agent, sum_cost, sum_tokens
from synthorg.memory.ranking import rank_memories
from synthorg.observability import scrub_event_fields
from tests.benchmarks._helpers import (
    NOW,
    RETRIEVAL_CONFIG,
    make_cost_record,
    make_memory_entry,
)

# Captured peaks (KiB) plus a 25% headroom factor for variance across
# Python 3.14 patch releases and runner architectures. Captured on
# Windows 11 Pro N (x86_64); Linux ubuntu-latest typically measures
# 10-15% lower due to allocator differences. ARM64 may differ further.
# ⚠️ Bump only with explicit user approval -- these are durable
# baseline contracts; CI flakes are not a reason to raise them.
_RANK_1000_PEAK_KIB_CEILING: Final[int] = 4000
_SCRUB_ADVERSARIAL_PEAK_KIB_CEILING: Final[int] = 50
_BUDGET_AGG_2000_PEAK_KIB_CEILING: Final[int] = 2000

_KIB: Final[int] = 1024


def _peak_kib(thunk: Callable[[], object]) -> int:
    """Return the peak heap KiB used during ``thunk()``.

    The ``try``/``finally`` guarantees ``tracemalloc.stop()`` runs even
    if ``thunk`` raises, so the global tracemalloc state never leaks
    into subsequent tests.
    """
    tracemalloc.start()
    try:
        thunk()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak // _KIB


@pytest.mark.unit
def test_rank_memories_1000_peak_heap() -> None:
    """Ranking 1000 entries must stay under the captured peak ceiling."""
    entries = tuple(
        make_memory_entry(i, age_hours=i * 0.1, relevance=0.4 + (i % 20) * 0.03)
        for i in range(1000)
    )

    def thunk() -> None:
        rank_memories(entries, config=RETRIEVAL_CONFIG, now=NOW)

    peak_kib = _peak_kib(thunk)
    assert peak_kib <= _RANK_1000_PEAK_KIB_CEILING, (
        f"rank_memories(1000) peak heap regressed: "
        f"{peak_kib} KiB > {_RANK_1000_PEAK_KIB_CEILING} KiB ceiling. "
        f"Bump the ceiling only with explicit approval."
    )


@pytest.mark.unit
def test_scrub_adversarial_peak_heap() -> None:
    """Scrubbing the adversarial 20-key payload must stay under ceiling."""
    payload = {
        "event": "API_AUTH_VERIFY_FAILED",
        "client_secret": "sk-very-secret-do-not-leak-12345",
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.p.s",
        "raw_body": "client_id=demo&client_secret=sk-leaked",
        "response_body": '{"access_token":"sk-leaked-12345"}',
        "fernet_blob": "gAAAAABl-not-actually-fernet-shaped",
        "params": ["redirect_uri=https://x.test/cb", "scope=read"],
        "user_agent": "synthorg-cli/0.7.3",
        "trace_id": "trace-abc",
        "duration_ms": 8.3,
    }

    def thunk() -> None:
        scrub_event_fields(None, "warning", payload.copy())

    peak_kib = _peak_kib(thunk)
    assert peak_kib <= _SCRUB_ADVERSARIAL_PEAK_KIB_CEILING, (
        f"scrub_event_fields adversarial peak heap regressed: "
        f"{peak_kib} KiB > {_SCRUB_ADVERSARIAL_PEAK_KIB_CEILING} KiB ceiling. "
        f"Bump the ceiling only with explicit approval."
    )


@pytest.mark.unit
def test_budget_aggregation_2000_peak_heap() -> None:
    """Group + sum across 2000 cost records must stay under ceiling."""
    agents = [f"agent-{a}" for a in range(20)]
    records = [
        make_cost_record(
            i,
            agent_id=agents[i % 20],
            cost=0.02 + (i % 30) * 0.003,
            input_tokens=200 + i * 5,
            output_tokens=80 + i * 3,
            hours_ago=i * 0.25,
        )
        for i in range(2000)
    ]

    def thunk() -> None:
        grouped = group_by_agent(records)
        for bucket in grouped.values():
            sum_cost(bucket)
            sum_tokens(bucket)

    peak_kib = _peak_kib(thunk)
    assert peak_kib <= _BUDGET_AGG_2000_PEAK_KIB_CEILING, (
        f"Budget aggregation peak heap regressed: "
        f"{peak_kib} KiB > {_BUDGET_AGG_2000_PEAK_KIB_CEILING} KiB ceiling. "
        f"Bump the ceiling only with explicit approval."
    )
