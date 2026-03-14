"""Per-pair delegation rate limiter."""

import time
from collections.abc import Callable  # noqa: TC003

from synthorg.communication.config import RateLimitConfig  # noqa: TC001
from synthorg.communication.loop_prevention._pair_key import pair_key
from synthorg.communication.loop_prevention.models import GuardCheckOutcome
from synthorg.observability import get_logger
from synthorg.observability.events.delegation import (
    DELEGATION_LOOP_RATE_LIMITED,
)

logger = get_logger(__name__)

_MECHANISM = "rate_limit"
_DEFAULT_WINDOW_SECONDS = 60.0


class DelegationRateLimiter:
    """Per-pair rate limit with burst allowance.

    The key is the sorted (a, b) agent pair. Counts delegations within
    the sliding window. The effective limit per window is
    ``max_per_pair_per_minute + burst_allowance``, giving additive
    headroom above the base rate.

    Args:
        config: Rate limit configuration.
        clock: Monotonic clock function for deterministic testing.
        window_seconds: Duration of the sliding window.  Defaults to
            60.0, matching the ``max_per_pair_per_minute`` semantics.
    """

    __slots__ = ("_clock", "_config", "_timestamps", "_window_seconds")

    def __init__(
        self,
        config: RateLimitConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._config = config
        self._clock = clock
        self._window_seconds = window_seconds
        self._timestamps: dict[tuple[str, str], list[float]] = {}

    def check(
        self,
        delegator_id: str,
        delegatee_id: str,
    ) -> GuardCheckOutcome:
        """Check whether the pair has exceeded the rate limit.

        Expired timestamps are pruned on every call.

        Args:
            delegator_id: ID of the delegating agent.
            delegatee_id: ID of the target agent.

        Returns:
            Outcome with passed=False if rate limit exceeded.
        """
        key = pair_key(delegator_id, delegatee_id)
        now = self._clock()
        cutoff = now - self._window_seconds
        timestamps = self._timestamps.get(key, [])
        recent = [t for t in timestamps if t > cutoff]
        # Prune expired entries on read; evict empty keys
        if recent:
            self._timestamps[key] = recent
        else:
            self._timestamps.pop(key, None)
        limit = self._config.max_per_pair_per_minute + self._config.burst_allowance
        if len(recent) >= limit:
            logger.info(
                DELEGATION_LOOP_RATE_LIMITED,
                delegator=delegator_id,
                delegatee=delegatee_id,
                count=len(recent),
                limit=limit,
            )
            return GuardCheckOutcome(
                passed=False,
                mechanism=_MECHANISM,
                message=(
                    f"Rate limit exceeded for pair "
                    f"({delegator_id!r}, {delegatee_id!r}): "
                    f"{len(recent)} delegations in last "
                    f"{self._window_seconds:.0f}s "
                    f"(limit {limit})"
                ),
            )
        return GuardCheckOutcome(passed=True, mechanism=_MECHANISM)

    def record(
        self,
        delegator_id: str,
        delegatee_id: str,
    ) -> None:
        """Record a delegation timestamp for the pair.

        Args:
            delegator_id: ID of the delegating agent.
            delegatee_id: ID of the target agent.
        """
        key = pair_key(delegator_id, delegatee_id)
        now = self._clock()
        cutoff = now - self._window_seconds
        timestamps = self._timestamps.get(key, [])
        # Prune expired entries on write; add new timestamp
        recent = [t for t in timestamps if t > cutoff]
        recent.append(now)
        self._timestamps[key] = recent
