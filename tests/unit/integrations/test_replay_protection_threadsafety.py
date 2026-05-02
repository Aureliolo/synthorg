"""Thread-safety tests for ReplayProtector.

Two identical webhook payloads arriving simultaneously must not both
pass the nonce duplicate check. The ``threading.Lock`` around the
check-and-insert block guarantees exactly one accept per nonce.
"""

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

        def attempt() -> bool:
            return protector.check(nonce="duplicate-nonce", timestamp=ts)

        with ThreadPoolExecutor(max_workers=16) as pool:
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

        def attempt(i: int) -> bool:
            return protector.check(nonce=f"nonce-{i}", timestamp=ts)

        with ThreadPoolExecutor(max_workers=16) as pool:
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

        def attempt(i: int) -> None:
            protector.check(nonce=f"nonce-{i}", timestamp=ts)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(attempt, i) for i in range(128)]
            for f in futures:
                f.result()
        # The bounded store must stay within max_entries even under
        # concurrent insert pressure.
        assert len(protector._seen) <= 8
