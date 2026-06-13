"""General resilience primitives shared across services and workers.

The ``providers/resilience`` package is coupled to ``ProviderError``
semantics and provider-specific retry classification.  This package
holds the cross-cutting primitives that other layers (NATS publishes,
HTTP shippers, parse-failure self-correction loops) reach for when
they need bounded retry without a provider-error contract.
"""

from synthorg.core.resilience.general_retry import GeneralRetryHandler
from synthorg.core.resilience.retry_after import coerce_finite_nonneg_seconds
from synthorg.core.resilience.sliding_window import (
    SlidingWindowEventLimiter,
    build_revalidation_limiter,
)

__all__ = [
    "GeneralRetryHandler",
    "SlidingWindowEventLimiter",
    "build_revalidation_limiter",
    "coerce_finite_nonneg_seconds",
]
