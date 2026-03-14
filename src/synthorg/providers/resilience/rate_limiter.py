"""Client-side rate limiter with RPM and concurrency controls."""

import asyncio
import math
import time

from synthorg.core.resilience_config import RateLimiterConfig  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_RATE_LIMITER_PAUSED,
    PROVIDER_RATE_LIMITER_THROTTLED,
)

logger = get_logger(__name__)


class RateLimiter:
    """Client-side rate limiter with RPM tracking and concurrency control.

    Uses a sliding window for RPM tracking and an asyncio semaphore for
    concurrency limiting.  Supports pause-until from provider
    ``retry_after`` hints.

    Args:
        config: Rate limiter configuration.
        provider_name: Provider name for logging context.
    """

    def __init__(
        self,
        config: RateLimiterConfig,
        *,
        provider_name: str,
    ) -> None:
        self._config = config
        self._provider_name = provider_name
        self._semaphore: asyncio.Semaphore | None = (
            asyncio.Semaphore(config.max_concurrent)
            if config.max_concurrent > 0
            else None
        )
        self._request_timestamps: list[float] = []
        self._pause_until: float = 0.0
        self._rpm_lock: asyncio.Lock = asyncio.Lock()

    @property
    def is_enabled(self) -> bool:
        """Whether any rate limiting is active."""
        return (
            self._config.max_requests_per_minute > 0 or self._config.max_concurrent > 0
        )

    async def acquire(self) -> None:
        """Wait for an available slot.

        Blocks until both the RPM window and concurrency semaphore
        allow a new request.  Also respects any active pause.
        """
        if not self.is_enabled and self._pause_until <= time.monotonic():
            return

        # Respect pause-until from retry_after.
        # Re-check in a loop in case pause() extends _pause_until while sleeping.
        while True:
            now = time.monotonic()
            remaining = self._pause_until - now
            if remaining <= 0:
                break
            logger.info(
                PROVIDER_RATE_LIMITER_THROTTLED,
                provider=self._provider_name,
                wait_seconds=round(remaining, 2),
                reason="pause_active",
            )
            await asyncio.sleep(remaining)

        # RPM sliding window
        if self._config.max_requests_per_minute > 0:
            await self._wait_for_rpm_slot()

        # Concurrency semaphore
        if self._semaphore is not None:
            await self._semaphore.acquire()

    def release(self) -> None:
        """Release a concurrency slot."""
        if self._semaphore is not None:
            self._semaphore.release()

    def pause(self, seconds: float) -> None:
        """Block new requests for *seconds*.

        Called when a ``RateLimitError`` with ``retry_after`` is received.
        Multiple calls take the latest pause-until if it extends further.

        Args:
            seconds: Duration to pause in seconds.  Must be finite and
                non-negative.

        Raises:
            ValueError: If *seconds* is negative or not finite.
        """
        if not math.isfinite(seconds) or seconds < 0:
            msg = f"pause seconds must be a finite non-negative number, got {seconds!r}"
            raise ValueError(msg)
        new_until = time.monotonic() + seconds
        if new_until > self._pause_until:
            self._pause_until = new_until
            logger.info(
                PROVIDER_RATE_LIMITER_PAUSED,
                provider=self._provider_name,
                pause_seconds=round(seconds, 2),
            )

    async def _wait_for_rpm_slot(self) -> None:
        """Wait until a slot is available in the RPM window.

        Uses a lock to prevent concurrent coroutines from both seeing
        an available slot and over-committing the window.
        """
        rpm = self._config.max_requests_per_minute
        window = 60.0

        while True:
            async with self._rpm_lock:
                now = time.monotonic()
                cutoff = now - window

                # Prune timestamps outside the window
                self._request_timestamps = [
                    t for t in self._request_timestamps if t > cutoff
                ]

                if len(self._request_timestamps) < rpm:
                    self._request_timestamps.append(now)
                    return

                # Wait until the oldest timestamp expires
                oldest = self._request_timestamps[0]
                wait = oldest - cutoff

            # Sleep outside the lock so other coroutines can proceed.
            if wait > 0:
                logger.debug(
                    PROVIDER_RATE_LIMITER_THROTTLED,
                    provider=self._provider_name,
                    wait_seconds=round(wait, 2),
                    reason="rpm_limit",
                )
                await asyncio.sleep(wait)
