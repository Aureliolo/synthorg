"""Loop prevention mechanisms for delegation safety."""

from synthorg.communication.loop_prevention.ancestry import (
    check_ancestry,
)
from synthorg.communication.loop_prevention.circuit_breaker import (
    CircuitBreakerState,
    DelegationCircuitBreaker,
)
from synthorg.communication.loop_prevention.dedup import (
    DelegationDeduplicator,
)
from synthorg.communication.loop_prevention.depth import (
    check_delegation_depth,
)
from synthorg.communication.loop_prevention.guard import (
    DelegationGuard,
)
from synthorg.communication.loop_prevention.models import (
    GuardCheckOutcome,
)
from synthorg.communication.loop_prevention.rate_limit import (
    DelegationRateLimiter,
)

__all__ = [
    "CircuitBreakerState",
    "DelegationCircuitBreaker",
    "DelegationDeduplicator",
    "DelegationGuard",
    "DelegationRateLimiter",
    "GuardCheckOutcome",
    "check_ancestry",
    "check_delegation_depth",
]
