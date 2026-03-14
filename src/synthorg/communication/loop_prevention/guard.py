"""Delegation guard orchestrating all loop prevention mechanisms."""

from synthorg.communication.config import LoopPreventionConfig  # noqa: TC001
from synthorg.communication.loop_prevention.ancestry import check_ancestry
from synthorg.communication.loop_prevention.circuit_breaker import (
    DelegationCircuitBreaker,
)
from synthorg.communication.loop_prevention.dedup import (
    DelegationDeduplicator,
)
from synthorg.communication.loop_prevention.depth import (
    check_delegation_depth,
)
from synthorg.communication.loop_prevention.models import GuardCheckOutcome
from synthorg.communication.loop_prevention.rate_limit import (
    DelegationRateLimiter,
)
from synthorg.observability import get_logger
from synthorg.observability.events.delegation import (
    DELEGATION_LOOP_BLOCKED,
)

logger = get_logger(__name__)

_SUCCESS_MECHANISM = "all_passed"


class DelegationGuard:
    """Orchestrates all loop prevention mechanisms.

    Checks run in order: ancestry, depth, dedup, rate_limit,
    circuit_breaker. Short-circuits on the first failure.

    Args:
        config: Loop prevention configuration.
    """

    __slots__ = (
        "_circuit_breaker",
        "_config",
        "_deduplicator",
        "_rate_limiter",
    )

    def __init__(self, config: LoopPreventionConfig) -> None:
        self._config = config
        self._deduplicator = DelegationDeduplicator(
            window_seconds=config.dedup_window_seconds,
        )
        self._rate_limiter = DelegationRateLimiter(config.rate_limit)
        self._circuit_breaker = DelegationCircuitBreaker(
            config.circuit_breaker,
        )

    def check(
        self,
        delegation_chain: tuple[str, ...],
        delegator_id: str,
        delegatee_id: str,
        task_id: str,
    ) -> GuardCheckOutcome:
        """Run all loop prevention checks.

        Returns the first failing outcome, or a success outcome if
        all checks pass.

        Args:
            delegation_chain: Current delegation ancestry.
            delegator_id: ID of the delegating agent.
            delegatee_id: ID of the proposed delegatee.
            task_id: Unique ID of the task being delegated.

        Returns:
            First failing outcome or an all-passed success.
        """
        # Pure (stateless) checks first — sequential to short-circuit
        outcome = check_ancestry(delegation_chain, delegatee_id)
        if not outcome.passed:
            return self._log_and_return(outcome, delegator_id, delegatee_id)

        outcome = check_delegation_depth(
            delegation_chain,
            self._config.max_delegation_depth,
        )
        if not outcome.passed:
            return self._log_and_return(outcome, delegator_id, delegatee_id)

        # Stateful checks — only run if pure checks passed
        outcome = self._deduplicator.check(
            delegator_id,
            delegatee_id,
            task_id,
        )
        if not outcome.passed:
            return self._log_and_return(outcome, delegator_id, delegatee_id)

        outcome = self._rate_limiter.check(delegator_id, delegatee_id)
        if not outcome.passed:
            return self._log_and_return(outcome, delegator_id, delegatee_id)

        outcome = self._circuit_breaker.check(delegator_id, delegatee_id)
        if not outcome.passed:
            return self._log_and_return(outcome, delegator_id, delegatee_id)
        return GuardCheckOutcome(
            passed=True,
            mechanism=_SUCCESS_MECHANISM,
        )

    @staticmethod
    def _log_and_return(
        outcome: GuardCheckOutcome,
        delegator_id: str,
        delegatee_id: str,
    ) -> GuardCheckOutcome:
        """Log a blocked delegation and return the outcome."""
        logger.info(
            DELEGATION_LOOP_BLOCKED,
            mechanism=outcome.mechanism,
            delegator=delegator_id,
            delegatee=delegatee_id,
            message=outcome.message,
        )
        return outcome

    def record_delegation(
        self,
        delegator_id: str,
        delegatee_id: str,
        task_id: str,
    ) -> None:
        """Record a successful delegation in all stateful mechanisms.

        Each delegation between a pair contributes to the circuit breaker
        bounce count.  Back-and-forth patterns (A→B then B→A) both
        increment the same counter because the pair key is direction-
        agnostic, so repeated ping-pong will trip the breaker fastest.

        Args:
            delegator_id: ID of the delegating agent.
            delegatee_id: ID of the target agent.
            task_id: Unique ID of the delegated task.
        """
        self._deduplicator.record(delegator_id, delegatee_id, task_id)
        self._rate_limiter.record(delegator_id, delegatee_id)
        self._circuit_breaker.record_delegation(delegator_id, delegatee_id)
