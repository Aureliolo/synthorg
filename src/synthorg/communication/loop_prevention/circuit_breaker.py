"""Circuit breaker for delegation bounces between agent pairs."""

import time
from collections.abc import Callable  # noqa: TC003
from enum import StrEnum

from synthorg.communication.config import CircuitBreakerConfig  # noqa: TC001
from synthorg.communication.loop_prevention._pair_key import pair_key
from synthorg.communication.loop_prevention.models import GuardCheckOutcome
from synthorg.observability import get_logger
from synthorg.observability.events.delegation import (
    DELEGATION_LOOP_CIRCUIT_OPEN,
    DELEGATION_LOOP_CIRCUIT_RESET,
)

logger = get_logger(__name__)

_MECHANISM = "circuit_breaker"


class CircuitBreakerState(StrEnum):
    """State of the circuit breaker for an agent pair.

    Members:
        CLOSED: Normal operation, delegations allowed.
        OPEN: Blocked, cooldown period active.
    """

    CLOSED = "closed"
    OPEN = "open"


class _PairState:
    """Internal mutable state for a single agent pair.

    Attributes:
        bounce_count: Delegations since last reset.
        opened_at: Monotonic timestamp when opened, or ``None``.
    """

    __slots__ = ("bounce_count", "opened_at")

    def __init__(self) -> None:
        self.bounce_count: int = 0
        self.opened_at: float | None = None


class DelegationCircuitBreaker:
    """Tracks delegation bounces per sorted agent pair.

    After ``bounce_threshold`` bounces between the same pair, the
    circuit opens for ``cooldown_seconds``. While open, all delegation
    checks for that pair fail.

    Args:
        config: Circuit breaker configuration.
        clock: Monotonic clock function for deterministic testing.
    """

    __slots__ = ("_clock", "_config", "_pairs")

    def __init__(
        self,
        config: CircuitBreakerConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._clock = clock
        self._pairs: dict[tuple[str, str], _PairState] = {}

    def _get_pair(
        self,
        delegator_id: str,
        delegatee_id: str,
    ) -> _PairState | None:
        key = pair_key(delegator_id, delegatee_id)
        return self._pairs.get(key)

    def _get_or_create_pair(
        self,
        delegator_id: str,
        delegatee_id: str,
    ) -> _PairState:
        key = pair_key(delegator_id, delegatee_id)
        return self._pairs.setdefault(key, _PairState())

    def get_state(
        self,
        delegator_id: str,
        delegatee_id: str,
    ) -> CircuitBreakerState:
        """Get the circuit breaker state for an agent pair.

        If the circuit was previously open and the cooldown has expired,
        the pair state is reset before returning ``CLOSED``.

        Args:
            delegator_id: First agent ID.
            delegatee_id: Second agent ID.

        Returns:
            Current state of the circuit breaker.
        """
        pair = self._get_pair(delegator_id, delegatee_id)
        if pair is None:
            return CircuitBreakerState.CLOSED
        if pair.opened_at is not None:
            elapsed = self._clock() - pair.opened_at
            if elapsed < self._config.cooldown_seconds:
                return CircuitBreakerState.OPEN
            # Cooldown expired: evict the stale entry
            key = pair_key(delegator_id, delegatee_id)
            del self._pairs[key]
            logger.info(
                DELEGATION_LOOP_CIRCUIT_RESET,
                delegator=delegator_id,
                delegatee=delegatee_id,
                cooldown_seconds=self._config.cooldown_seconds,
            )
        return CircuitBreakerState.CLOSED

    def check(
        self,
        delegator_id: str,
        delegatee_id: str,
    ) -> GuardCheckOutcome:
        """Check whether delegation is allowed for this pair.

        Args:
            delegator_id: ID of the delegating agent.
            delegatee_id: ID of the target agent.

        Returns:
            Outcome with passed=False if circuit is open.
        """
        state = self.get_state(delegator_id, delegatee_id)
        if state is CircuitBreakerState.OPEN:
            logger.info(
                DELEGATION_LOOP_CIRCUIT_OPEN,
                delegator=delegator_id,
                delegatee=delegatee_id,
            )
            return GuardCheckOutcome(
                passed=False,
                mechanism=_MECHANISM,
                message=(
                    f"Circuit breaker open for pair "
                    f"({delegator_id!r}, {delegatee_id!r}); "
                    f"cooldown {self._config.cooldown_seconds}s"
                ),
            )
        return GuardCheckOutcome(passed=True, mechanism=_MECHANISM)

    def record_delegation(
        self,
        delegator_id: str,
        delegatee_id: str,
    ) -> None:
        """Record a delegation event for the pair.

        Each delegation between a pair increments the bounce counter.
        Back-and-forth patterns trip the breaker fastest because the
        key is direction-agnostic.  If the count reaches the threshold,
        the circuit opens.  If the circuit is already open (cooldown not
        yet expired), this call is a no-op.

        Args:
            delegator_id: First agent ID.
            delegatee_id: Second agent ID.
        """
        state = self.get_state(delegator_id, delegatee_id)
        if state is CircuitBreakerState.OPEN:
            return
        pair = self._get_or_create_pair(delegator_id, delegatee_id)
        pair.bounce_count += 1
        if pair.bounce_count >= self._config.bounce_threshold:
            pair.opened_at = self._clock()
            logger.warning(
                DELEGATION_LOOP_CIRCUIT_OPEN,
                delegator=delegator_id,
                delegatee=delegatee_id,
                bounce_count=pair.bounce_count,
                threshold=self._config.bounce_threshold,
            )
