"""Per-operation rate limiting and inflight concurrency guards.

Two layered guards sit on top of the global two-tier limiter in
``api/config.py`` ``RateLimitConfig``:

1. ``per_op_rate_limit`` -- sliding-window bucket per (operation,
   subject).  Throttles burst rate.
2. ``per_op_concurrency`` -- inflight counter per (operation, subject).
   Caps simultaneous long-running requests.  Enforced by
   ``PerOpConcurrencyMiddleware``.

Both subsystems follow CLAUDE.md pluggable-subsystems pattern: Protocol
+ strategy + factory + config discriminator, with safe defaults
(memory-backed) and Redis reserved for cross-worker fairness.  Ships
with an ``InMemorySlidingWindowStore`` + ``InMemoryInflightStore``
default; additional strategies can be added behind the factory.
"""

from synthorg.api.rate_limits.config import PerOpRateLimitConfig
from synthorg.api.rate_limits.factory import build_sliding_window_store
from synthorg.api.rate_limits.guard import per_op_rate_limit
from synthorg.api.rate_limits.in_memory import InMemorySlidingWindowStore
from synthorg.api.rate_limits.in_memory_inflight import InMemoryInflightStore
from synthorg.api.rate_limits.inflight_config import PerOpConcurrencyConfig
from synthorg.api.rate_limits.inflight_factory import build_inflight_store
from synthorg.api.rate_limits.inflight_guard import per_op_concurrency
from synthorg.api.rate_limits.inflight_middleware import (
    OPT_KEY as INFLIGHT_OPT_KEY,
)
from synthorg.api.rate_limits.inflight_middleware import (
    PerOpConcurrencyMiddleware,
)
from synthorg.api.rate_limits.inflight_protocol import InflightStore
from synthorg.api.rate_limits.policies import (
    INFLIGHT_POLICIES,
    RATE_LIMIT_POLICIES,
    per_op_concurrency_from_policy,
    per_op_rate_limit_from_policy,
)
from synthorg.api.rate_limits.protocol import (
    RateLimitOutcome,
    SlidingWindowStore,
)

__all__ = [
    "INFLIGHT_OPT_KEY",
    "INFLIGHT_POLICIES",
    "RATE_LIMIT_POLICIES",
    "InMemoryInflightStore",
    "InMemorySlidingWindowStore",
    "InflightStore",
    "PerOpConcurrencyConfig",
    "PerOpConcurrencyMiddleware",
    "PerOpRateLimitConfig",
    "RateLimitOutcome",
    "SlidingWindowStore",
    "build_inflight_store",
    "build_sliding_window_store",
    "per_op_concurrency",
    "per_op_concurrency_from_policy",
    "per_op_rate_limit",
    "per_op_rate_limit_from_policy",
]
