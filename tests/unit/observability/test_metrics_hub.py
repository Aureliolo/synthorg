"""Tests for the process-global active-collector accessor.

``metrics_hub._active`` resolves the weakref-held active collector for
every push-time ``record_*`` helper. The accessor must read the module
global exactly once: a concurrent ``clear_active_collector()`` landing
between an ``is None`` check and a second read would otherwise call
``None()`` and raise ``TypeError`` straight into the business path
(``_safe_record`` re-raises ``TypeError`` on purpose).
"""

import dis
import gc
from collections.abc import Iterator

import pytest

from synthorg.observability import metrics_hub
from synthorg.observability.prometheus_collector import PrometheusCollector


@pytest.fixture(autouse=True)
def _clear_active_collector() -> Iterator[None]:
    """Drop any process-active collector before AND after each test.

    Clearing after the test too keeps a collector set by one test from
    leaking into the next test (or another file's worker) when the test
    body fails before its own cleanup.
    """
    metrics_hub.clear_active_collector()
    yield
    metrics_hub.clear_active_collector()


@pytest.mark.unit
def test_active_loads_collector_ref_once() -> None:
    """The accessor reads ``_collector_ref`` once (no TOCTOU window).

    A second ``LOAD_GLOBAL _collector_ref`` would reintroduce the race
    where a concurrent clear nulls the slot between the two reads.
    """
    loads = [
        instr
        for instr in dis.get_instructions(metrics_hub._active)
        if instr.opname == "LOAD_GLOBAL" and instr.argval == "_collector_ref"
    ]
    assert len(loads) == 1, (
        "metrics_hub._active must capture _collector_ref into a local once; "
        f"found {len(loads)} LOAD_GLOBAL reads"
    )


@pytest.mark.unit
def test_active_returns_none_in_bootstrap() -> None:
    assert metrics_hub._active() is None


@pytest.mark.unit
def test_active_returns_registered_collector() -> None:
    collector = PrometheusCollector()
    metrics_hub.set_active_collector(collector)
    assert metrics_hub._active() is collector


@pytest.mark.unit
def test_active_returns_none_after_clear() -> None:
    collector = PrometheusCollector()
    metrics_hub.set_active_collector(collector)
    metrics_hub.clear_active_collector()
    assert metrics_hub._active() is None


@pytest.mark.unit
def test_active_returns_none_after_collector_gc() -> None:
    """A dead weakref target resolves to None without raising.

    The collector is held only by the module weakref; once the local
    strong reference is dropped and GC runs, ``_collector_ref()`` returns
    ``None`` and ``_active`` must surface that cleanly.
    """
    collector = PrometheusCollector()
    metrics_hub.set_active_collector(collector)
    del collector
    gc.collect()
    assert metrics_hub._active() is None
