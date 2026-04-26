"""tracemalloc-based heap-ceiling tests for Python hot paths.

These are unit tests (``@pytest.mark.unit``) that *assert* on peak
heap usage of a small set of high-fanout paths. They are NOT CodSpeed
benchmarks -- they live under ``tests/unit/perf/`` rather than
``tests/benchmarks/`` so the canonical "benchmark suite is opt-in via
``--codspeed``" rule stays clean: heap-ceiling tests run on every
``pytest -m unit -n 8`` invocation alongside the rest of the unit
suite.

They complement the CodSpeed CPU-instruction benches: a regression
that doubles peak heap (e.g. accidentally building a list when a
generator would do) is invisible to instruction-counting benches but
very visible to users running on memory-constrained containers.

Ceilings were captured 2026-04-26 on the first PR-1637 CI run
(ubuntu-latest x86_64) and carry headroom for variance. Bump only
with explicit user approval.
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

# Builders + constants are imported from ``tests/benchmarks/_helpers.py``
# rather than re-defined here; the helpers are deterministic / pure
# (no fixture/scope state) and shared between the CodSpeed benches
# and these heap-ceiling tests.

# Captured peaks (KiB) sized to absorb cross-platform allocator
# variance. Python tracemalloc's accounting differs significantly
# between Windows (smaller; misses some kernel allocations) and
# Linux (larger; accounts for slab/arena overhead). The CI gate runs
# on ubuntu-latest, so each ceiling is set at:
#   ceil(Linux-measured * ~1.5)
# which keeps the gate meaningful on the slower-allocator platform
# while staying loose enough to absorb minor Python 3.14 patch
# variance. Linux ubuntu-latest reference values (captured 2026-04-26
# on the first PR-1637 CI run):
#   rank_memories(1000)     : ~3.0 MiB peak
#   scrub_adversarial(20-k) : ~330 KiB peak (much higher than Windows
#                             due to dict + regex arena overhead +
#                             structlog processor allocations)
#   budget_aggregation(2000): ~1.5 MiB peak
# ⚠️ Bump only with explicit user approval -- these are durable
# baseline contracts; CI flakes are not a reason to raise them.
_RANK_1000_PEAK_KIB_CEILING: Final[int] = 4500
_SCRUB_ADVERSARIAL_PEAK_KIB_CEILING: Final[int] = 500
_BUDGET_AGG_2000_PEAK_KIB_CEILING: Final[int] = 2200

_KIB: Final[int] = 1024


def _peak_kib(thunk: Callable[[], object]) -> int:
    """Return the peak heap KiB used during ``thunk()``.

    The ``try``/``finally`` guarantees ``tracemalloc.stop()`` runs even
    if ``thunk`` raises, so the global tracemalloc state never leaks
    into subsequent tests.

    Conversion is ``ceil(peak / KiB)`` -- a regression of 1..1023 bytes
    above the ceiling must NOT pass under integer floor division. The
    ceiling fields are themselves whole-KiB budgets, so the rounding
    direction matters at the single-byte boundary.
    """
    tracemalloc.start()
    try:
        thunk()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return -(-peak // _KIB)


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
