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


def test_eviction_logs_exactly_once_under_thread_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the eviction warning fires exactly once across 1000
    concurrent ``on_event`` calls -- not just that the boolean flag
    eventually flipped (which would also pass if the log fired N
    times)."""
    from synthorg.telemetry import event_counter as ec_mod

    counter = InMemoryTelemetryEventCounter(max_events=10)

    # Pre-fill to capacity so the very first concurrent on_event triggers
    # the eviction-log code path.
    now = datetime.now(UTC)
    for i in range(10):
        counter.on_event(_FakeEvent(now, f"prefill.{i}"))  # type: ignore[arg-type]

    assert counter._eviction_logged is False

    info_calls: list[str] = []
    original_info = ec_mod.logger.info

    def _spy(event: str, **kwargs: object) -> object:
        info_calls.append(event)
        return original_info(event, **kwargs)

    monkeypatch.setattr(ec_mod.logger, "info", _spy)

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

    eviction_logs = [e for e in info_calls if e == "telemetry.counter.evicted"]
    assert len(eviction_logs) == 1, (
        f"Expected exactly one eviction log; saw {len(eviction_logs)}"
    )
    assert getattr(counter, "_eviction_logged") is True  # noqa: B009
