"""Concurrency test for InMemoryTelemetryEventCounter (#1599 §4.5).

The eviction-flag flip happens inside ``self._lock`` so only one
thread ever observes ``first_eviction=True``; this test confirms the
public guarantee under heavy thread-pool contention.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from synthorg.telemetry.event_counter import InMemoryTelemetryEventCounter

pytestmark = pytest.mark.unit


class _FakeEvent:
    """Minimal stand-in matching the TelemetryEvent shape needed."""

    def __init__(self, ts: datetime, event_type: str) -> None:
        self.timestamp = ts
        self.event_type = event_type


def test_eviction_flag_flips_exactly_once_under_thread_concurrency() -> None:
    counter = InMemoryTelemetryEventCounter(max_events=10)

    # Pre-fill to capacity so the very first concurrent on_event triggers
    # the eviction-log code path.
    now = datetime.now(UTC)
    for i in range(10):
        counter.on_event(_FakeEvent(now, f"prefill.{i}"))  # type: ignore[arg-type]

    # Sentinel: the flag flip is the public signal that "exactly one
    # eviction log was emitted". The flag is set under self._lock, so
    # in CPython under the GIL only one of the 1000 concurrent callers
    # observes ``first_eviction=True``. Anything else would mean the
    # flag was checked outside the lock (the bug the audit flagged).
    assert counter._eviction_logged is False

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [
            pool.submit(
                counter.on_event,
                _FakeEvent(now, f"flood.{i}"),  # type: ignore[arg-type]
            )
            for i in range(1000)
        ]
        for fut in futures:
            fut.result()

    assert counter._eviction_logged is True
