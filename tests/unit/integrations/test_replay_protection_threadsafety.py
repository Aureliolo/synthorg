"""Thread-safety tests for ReplayProtector.

Two identical webhook payloads arriving simultaneously must not both
pass the nonce duplicate check. The ``threading.Lock`` around the
check-and-insert block guarantees exactly one accept per nonce.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from synthorg.integrations.webhooks.replay_protection import ReplayProtector
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


def _epoch(clock: FakeClock) -> float:
    return clock.now().timestamp()


class TestReplayProtectorThreadSafety:
    """Concurrent thread access must remain safe."""

    def test_concurrent_identical_nonces_yield_single_accept(self) -> None:
        clock = FakeClock()
        protector = ReplayProtector(window_seconds=300, clock=clock)
        ts = _epoch(clock)
        # ``Barrier`` ensures every worker has been scheduled and is
        # parked at the same instruction before any of them reaches
        # ``protector.check``. Without it ``ThreadPoolExecutor.submit``
        # spawns workers staggered, so the first attempt frequently
        # finishes the check-and-insert before the rest even hit the
        # lock -- which means the test passes even when the lock is
        # ineffective.
        barrier = threading.Barrier(64)

        def attempt() -> bool:
            # ``timeout`` keeps the barrier from holding the test
            # process indefinitely if a worker never arrives (e.g.
            # interpreter crash, GIL deadlock). A broken barrier
            # bubbles up via ``BrokenBarrierError`` and the future's
            # ``result()`` re-raises it -- a fast, diagnosable
            # failure in place of a CI hang.
            barrier.wait(timeout=5)
            return protector.check(nonce="duplicate-nonce", timestamp=ts)

        with ThreadPoolExecutor(max_workers=64) as pool:
            futures = [pool.submit(attempt) for _ in range(64)]
            results = [f.result() for f in futures]

        accepts = [r for r in results if r]
        rejects = [r for r in results if not r]
        assert len(accepts) == 1
        assert len(rejects) == 63

    def test_concurrent_distinct_nonces_all_accepted(self) -> None:
        clock = FakeClock()
        protector = ReplayProtector(window_seconds=300, clock=clock)
        ts = _epoch(clock)
        barrier = threading.Barrier(64)

        def attempt(i: int) -> bool:
            barrier.wait(timeout=5)
            return protector.check(nonce=f"nonce-{i}", timestamp=ts)

        with ThreadPoolExecutor(max_workers=64) as pool:
            futures = [pool.submit(attempt, i) for i in range(64)]
            results = [f.result() for f in futures]

        assert all(results)

    def test_concurrent_eviction_does_not_corrupt(self) -> None:
        clock = FakeClock()
        protector = ReplayProtector(
            window_seconds=300,
            max_entries=8,
            clock=clock,
        )
        ts = _epoch(clock)
        barrier = threading.Barrier(128)

        def attempt(i: int) -> None:
            barrier.wait(timeout=5)
            protector.check(nonce=f"nonce-{i}", timestamp=ts)

        with ThreadPoolExecutor(max_workers=128) as pool:
            futures = [pool.submit(attempt, i) for i in range(128)]
            for f in futures:
                f.result()
        # The bounded store must stay within max_entries even under
        # concurrent insert pressure.
        assert len(protector._seen) <= 8
