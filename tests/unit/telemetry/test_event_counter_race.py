"""Concurrency test for InMemoryTelemetryEventCounter.

The eviction-flag flip happens inside ``self._lock`` so only one
thread ever observes ``first_eviction=True``; this test confirms the
public guarantee under heavy thread-pool contention.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import pytest

from synthorg.telemetry.event_counter import InMemoryTelemetryEventCounter

pytestmark = pytest.mark.unit

# Bounds the per-future wait so a regression that hangs ``on_event``
# under contention fails fast instead of stalling the whole suite.
# 30 s is the global pytest timeout in pyproject.toml; keep this well
# below to leave headroom for the assertions after the join.
_FUTURE_TIMEOUT_S: float = 20.0

# Worker count chosen high enough to force genuine contention on the
# eviction lock (every worker tries to acquire under a single gate
# release) without overwhelming the xdist runner; matches the order of
# magnitude used by the surrounding race-test suite.
_WORKER_COUNT: int = 16


class _FakeEvent:
    """Minimal stand-in matching the TelemetryEvent shape needed."""

    def __init__(self, ts: datetime, event_type: str) -> None:
        self.timestamp = ts
        self.event_type = event_type


def test_eviction_logs_exactly_once_under_thread_concurrency() -> None:
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
    proxy = ec_mod.logger
    original_info = proxy.info

    def _spy(*args: Any, **kwargs: Any) -> Any:
        # Accept any structlog call shape (positional or keyword
        # event=...) so an unrelated logger.info elsewhere in the
        # under-test code path cannot blow this fixture up with a
        # TypeError. Record the message robustly: first positional arg
        # if present, else ``event`` kwarg, else a joined fallback.
        if args:
            info_calls.append(str(args[0]))
        elif "event" in kwargs:
            info_calls.append(str(kwargs["event"]))
        else:
            info_calls.append(" ".join(str(a) for a in args))
        return original_info(*args, **kwargs)

    # Direct setattr + try/finally delattr -- not monkeypatch.setattr
    # -- because ``proxy`` is a ``BoundLoggerLazyProxy`` whose
    # ``info`` attribute is normally served by ``__getattr__`` and is
    # NOT in the instance ``__dict__``. ``monkeypatch.setattr``
    # captures a snapshot via ``getattr`` (a bound method on the
    # current ``BoundLogger``) and "restores" it at teardown via
    # ``setattr``, permanently shadowing ``__getattr__`` for the
    # lifetime of the proxy. ``capture_logs()`` later swaps
    # ``_CONFIG.default_processors`` but the cached bound method
    # already holds its own processor list, so events bypass the
    # capture buffer entirely.
    from contextlib import suppress

    proxy.info = _spy  # type: ignore[method-assign]
    try:
        # ``start_evt`` blocks every worker until all futures are
        # queued, then a single ``set()`` releases them simultaneously.
        # Without the gate, the first submissions can finish before
        # the last is scheduled, which leaves the test asserting
        # sequential -- not concurrent -- behaviour and silently
        # accepts a regression.
        start_evt = threading.Event()

        def _gated_on_event(event: _FakeEvent) -> None:
            start_evt.wait()
            counter.on_event(event)  # type: ignore[arg-type]

        with ThreadPoolExecutor(max_workers=_WORKER_COUNT) as pool:
            futures = [
                pool.submit(_gated_on_event, _FakeEvent(now, f"flood.{i}"))
                for i in range(1000)
            ]
            # Release the gate only after every future is queued.
            start_evt.set()
            for fut in futures:
                # Bounded ``result()`` so a regression that hangs a worker
                # fails fast rather than blocking the suite indefinitely.
                fut.result(timeout=_FUTURE_TIMEOUT_S)

        eviction_logs = [e for e in info_calls if e == "telemetry.counter.evicted"]
        assert len(eviction_logs) == 1, (
            f"Expected exactly one eviction log; saw {len(eviction_logs)}"
        )
        assert getattr(counter, "_eviction_logged") is True  # noqa: B009
    finally:
        with suppress(AttributeError):
            del proxy.info
