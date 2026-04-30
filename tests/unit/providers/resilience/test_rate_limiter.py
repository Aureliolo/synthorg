"""Tests for RateLimiter."""

import asyncio
import contextlib

import pytest
import structlog

from synthorg.core.resilience_config import RateLimiterConfig
from synthorg.observability.events.provider import (
    PROVIDER_RATE_LIMITER_PAUSED,
    PROVIDER_RATE_LIMITER_THROTTLED,
)
from synthorg.providers.resilience.rate_limiter import RateLimiter
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


class TestRateLimiterDisabled:
    async def test_disabled_by_default(self) -> None:
        limiter = RateLimiter(
            RateLimiterConfig(),
            provider_name="test-provider",
        )
        assert limiter.is_enabled is False

    async def test_acquire_release_noop_when_disabled(self) -> None:
        limiter = RateLimiter(
            RateLimiterConfig(),
            provider_name="test-provider",
        )
        await limiter.acquire()
        limiter.release()  # should not raise


class TestRateLimiterConcurrency:
    async def test_concurrent_limit(self) -> None:
        config = RateLimiterConfig(max_concurrent=2)
        limiter = RateLimiter(config, provider_name="test-provider")
        assert limiter.is_enabled is True

        # Acquire 2 slots
        await limiter.acquire()
        await limiter.acquire()

        # Third acquire should block; verify with a short timeout
        acquired = asyncio.Event()

        async def _try_acquire() -> None:
            await limiter.acquire()
            acquired.set()

        task = asyncio.create_task(_try_acquire())
        try:
            # Yield control so _try_acquire starts and blocks on semaphore
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not acquired.is_set()

            # Release one slot and yield so the blocked task can proceed
            limiter.release()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert acquired.is_set()

            # Release the remaining two slots
            limiter.release()
            limiter.release()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_release_without_acquire_does_not_crash(self) -> None:
        config = RateLimiterConfig(max_concurrent=2)
        limiter = RateLimiter(config, provider_name="test-provider")
        # Extra release (semaphore goes above initial count, but doesn't crash)
        limiter.release()


class TestRateLimiterRPM:
    async def test_rpm_enabled(self) -> None:
        config = RateLimiterConfig(max_requests_per_minute=60)
        limiter = RateLimiter(config, provider_name="test-provider")
        assert limiter.is_enabled is True

    async def test_rpm_allows_within_limit(self) -> None:
        config = RateLimiterConfig(max_requests_per_minute=100)
        limiter = RateLimiter(config, provider_name="test-provider")

        # Should be able to acquire many times quickly
        for _ in range(10):
            await limiter.acquire()


class TestRateLimiterPause:
    async def test_pause_blocks_acquire(self) -> None:
        """acquire() sleeps for the remaining pause duration."""
        clock = FakeClock()
        config = RateLimiterConfig(max_concurrent=10)
        limiter = RateLimiter(config, provider_name="test-provider", clock=clock)

        limiter.pause(0.1)
        await limiter.acquire()

        # FakeClock.sleep advances virtual time by the requested
        # duration, so the acquire() loop converges in one iteration.
        assert len(clock.sleep_calls) == 1
        assert clock.sleep_calls[0] > 0
        limiter.release()

    async def test_pause_extends_if_longer(self) -> None:
        """A longer second pause extends the pause window."""
        clock = FakeClock()
        config = RateLimiterConfig(max_concurrent=10)
        limiter = RateLimiter(config, provider_name="test-provider", clock=clock)

        limiter.pause(0.05)
        limiter.pause(0.15)  # extends
        await limiter.acquire()

        # The remaining-pause sleep should reflect the longer window.
        assert any(s > 0.10 for s in clock.sleep_calls)
        limiter.release()

    async def test_pause_no_extend_if_shorter(self) -> None:
        """A shorter second pause does not reduce the pause window."""
        clock = FakeClock()
        config = RateLimiterConfig(max_concurrent=10)
        limiter = RateLimiter(config, provider_name="test-provider", clock=clock)

        limiter.pause(0.15)
        limiter.pause(0.01)  # shorter, should not reduce
        await limiter.acquire()

        # Should have waited the original 0.15s, not the shorter
        # 0.01s second-pause.
        assert any(s > 0.10 for s in clock.sleep_calls)
        limiter.release()

    async def test_pause_rejects_negative(self) -> None:
        limiter = RateLimiter(RateLimiterConfig(), provider_name="test-provider")
        with pytest.raises(ValueError, match="finite non-negative"):
            limiter.pause(-1.0)

    async def test_pause_rejects_inf(self) -> None:
        limiter = RateLimiter(RateLimiterConfig(), provider_name="test-provider")
        with pytest.raises(ValueError, match="finite non-negative"):
            limiter.pause(float("inf"))

    async def test_pause_rejects_nan(self) -> None:
        limiter = RateLimiter(RateLimiterConfig(), provider_name="test-provider")
        with pytest.raises(ValueError, match="finite non-negative"):
            limiter.pause(float("nan"))


class TestRateLimiterRPMThrottling:
    async def test_rpm_throttles_when_over_limit(self) -> None:
        """acquire() sleeps when RPM budget is exhausted, then retries."""
        clock = FakeClock()
        config = RateLimiterConfig(max_requests_per_minute=1)
        limiter = RateLimiter(config, provider_name="test-provider", clock=clock)

        # Fill the single RPM slot.
        await limiter.acquire()

        # Second acquire must sleep (budget exhausted). Under FakeClock
        # the acquire-loop call to clock.sleep advances virtual time
        # past the 60 s window, so the next iteration prunes the
        # filled slot and admits the request.
        await limiter.acquire()

        assert any(s > 0 for s in clock.sleep_calls)

    async def test_rpm_throttle_logs_rpm_limit_reason(self) -> None:
        """RPM throttling emits a log entry with reason='rpm_limit'."""
        clock = FakeClock()
        config = RateLimiterConfig(max_requests_per_minute=1)
        limiter = RateLimiter(config, provider_name="test-provider", clock=clock)

        await limiter.acquire()

        with structlog.testing.capture_logs() as cap:
            await limiter.acquire()

        rpm_logs = [e for e in cap if e.get("reason") == "rpm_limit"]
        assert len(rpm_logs) >= 1


class TestRateLimiterLogging:
    async def test_logs_pause(self) -> None:
        config = RateLimiterConfig(max_concurrent=10)
        limiter = RateLimiter(config, provider_name="test-provider")

        with structlog.testing.capture_logs() as cap:
            limiter.pause(1.0)

        paused = [e for e in cap if e.get("event") == PROVIDER_RATE_LIMITER_PAUSED]
        assert len(paused) == 1
        assert paused[0]["provider"] == "test-provider"

    async def test_logs_throttle_on_pause_active(self) -> None:
        config = RateLimiterConfig(max_concurrent=10)
        limiter = RateLimiter(config, provider_name="test-provider")

        limiter.pause(0.05)
        with structlog.testing.capture_logs() as cap:
            await limiter.acquire()

        throttled = [
            e for e in cap if e.get("event") == PROVIDER_RATE_LIMITER_THROTTLED
        ]
        assert len(throttled) >= 1
        assert throttled[0]["reason"] == "pause_active"

        limiter.release()
