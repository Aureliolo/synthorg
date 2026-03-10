"""Tests for RateLimiter."""

import asyncio
import contextlib
import time

import pytest
import structlog

from ai_company.core.resilience_config import RateLimiterConfig
from ai_company.observability.events.provider import (
    PROVIDER_RATE_LIMITER_PAUSED,
    PROVIDER_RATE_LIMITER_THROTTLED,
)
from ai_company.providers.resilience.rate_limiter import RateLimiter

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
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


@pytest.mark.unit
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
            await asyncio.sleep(0.05)
            assert not acquired.is_set()

            # Release one slot
            limiter.release()
            await asyncio.sleep(0.05)
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


@pytest.mark.unit
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


@pytest.mark.unit
class TestRateLimiterPause:
    async def test_pause_blocks_acquire(self) -> None:
        config = RateLimiterConfig(max_concurrent=10)
        limiter = RateLimiter(config, provider_name="test-provider")

        limiter.pause(0.1)
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start

        assert 0.07 <= elapsed <= 1.0  # must wait at least 70ms, not forever

        limiter.release()

    async def test_pause_extends_if_longer(self) -> None:
        config = RateLimiterConfig(max_concurrent=10)
        limiter = RateLimiter(config, provider_name="test-provider")

        limiter.pause(0.05)
        limiter.pause(0.15)  # extends

        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start

        assert 0.10 <= elapsed <= 1.0  # should wait for the longer pause

        limiter.release()

    async def test_pause_no_extend_if_shorter(self) -> None:
        config = RateLimiterConfig(max_concurrent=10)
        limiter = RateLimiter(config, provider_name="test-provider")

        limiter.pause(0.15)
        limiter.pause(0.01)  # shorter, should not reduce

        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start

        assert 0.10 <= elapsed <= 1.0

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


@pytest.mark.unit
class TestRateLimiterRPMThrottling:
    async def test_rpm_throttles_when_over_limit(self) -> None:
        """acquire() sleeps when RPM budget is exhausted, then retries."""
        from unittest import mock

        config = RateLimiterConfig(max_requests_per_minute=1)
        limiter = RateLimiter(config, provider_name="test-provider")

        base_t = 1_000_000.0
        slept = False

        async def instant_sleep(seconds: float) -> None:
            nonlocal slept
            slept = True

        def time_fn() -> float:
            return base_t if not slept else base_t + 61.0

        # Fill the single RPM slot
        with mock.patch(
            "ai_company.providers.resilience.rate_limiter.time.monotonic",
            time_fn,
        ):
            await limiter.acquire()

        # Second acquire must sleep (budget exhausted)
        with (
            mock.patch(
                "ai_company.providers.resilience.rate_limiter.time.monotonic",
                time_fn,
            ),
            mock.patch(
                "ai_company.providers.resilience.rate_limiter.asyncio.sleep",
                instant_sleep,
            ),
        ):
            await limiter.acquire()

        assert slept

    async def test_rpm_throttle_logs_rpm_limit_reason(self) -> None:
        """RPM throttling emits a log entry with reason='rpm_limit'."""
        from unittest import mock

        config = RateLimiterConfig(max_requests_per_minute=1)
        limiter = RateLimiter(config, provider_name="test-provider")

        base_t = 2_000_000.0
        slept = False

        async def instant_sleep(seconds: float) -> None:
            nonlocal slept
            slept = True

        def time_fn() -> float:
            return base_t if not slept else base_t + 61.0

        with mock.patch(
            "ai_company.providers.resilience.rate_limiter.time.monotonic",
            time_fn,
        ):
            await limiter.acquire()

        with (
            mock.patch(
                "ai_company.providers.resilience.rate_limiter.time.monotonic",
                time_fn,
            ),
            mock.patch(
                "ai_company.providers.resilience.rate_limiter.asyncio.sleep",
                instant_sleep,
            ),
            structlog.testing.capture_logs() as cap,
        ):
            await limiter.acquire()

        rpm_logs = [e for e in cap if e.get("reason") == "rpm_limit"]
        assert len(rpm_logs) >= 1


@pytest.mark.unit
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
