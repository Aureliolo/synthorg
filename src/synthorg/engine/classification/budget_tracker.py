"""Per-classification-run LLM cost budget tracker.

Simple async-safe in-memory tracker that accumulates cost across
semantic detector invocations within a single classification run.
Not persisted -- one instance per ``classify_execution_errors``
call.  All check-and-reserve operations are guarded by an
``asyncio.Lock`` so concurrent semantic detectors (e.g. siblings in
a ``CompositeDetector``) cannot race through the admission gate
and overspend the budget.
"""

import asyncio
import math

from synthorg.observability import get_logger
from synthorg.observability.events.classification import (
    DETECTOR_BUDGET_EXHAUSTED,
    DETECTOR_COST_INCURRED,
    INVALID_BUDGET,
    INVALID_COST,
)

logger = get_logger(__name__)


class ClassificationBudgetTracker:
    """Tracks LLM spend within a single classification run.

    Provides atomic check-and-reserve admission (``try_reserve``)
    plus settlement of the actual cost once the LLM call completes
    (``settle``).  ``release`` returns a reservation when the call
    never happened.  All three operations are serialised under the
    same lock.

    Args:
        budget: Maximum allowed spend for this run.

    Raises:
        ValueError: If ``budget`` is negative.
    """

    def __init__(self, budget: float) -> None:
        if not math.isfinite(budget) or budget < 0:
            msg = "budget must be a finite non-negative number"
            logger.error(
                INVALID_BUDGET,
                budget=budget,
                reason=msg,
            )
            raise ValueError(msg)
        self._budget = budget
        self._spent = 0.0
        self._lock = asyncio.Lock()

    async def try_reserve(self, estimated_cost: float) -> bool:
        """Atomically reserve budget for an upcoming LLM call.

        Acquires the internal lock, checks whether ``estimated_cost``
        fits within the remaining budget, and if so increments
        ``_spent`` by the estimate.  Returns ``True`` when the
        reservation succeeded, ``False`` when the budget is
        exhausted.  The reservation MUST be paired with a call to
        :meth:`settle` once the actual cost is known so the
        difference between estimate and actual can be applied.

        Args:
            estimated_cost: Estimated cost for the next LLM call.

        Returns:
            True when the reservation was recorded, False when the
            call would exceed the remaining budget.

        Raises:
            ValueError: If ``estimated_cost`` is negative.
        """
        if not math.isfinite(estimated_cost) or estimated_cost < 0:
            msg = "estimated_cost must be a finite non-negative number"
            logger.warning(
                INVALID_COST,
                cost=estimated_cost,
                kind="estimated",
                reason=msg,
            )
            raise ValueError(msg)
        async with self._lock:
            if self._spent + estimated_cost > self._budget:
                logger.info(
                    DETECTOR_BUDGET_EXHAUSTED,
                    spent=self._spent,
                    budget=self._budget,
                    estimated_cost=estimated_cost,
                )
                return False
            self._spent += estimated_cost
            return True

    async def settle(
        self,
        estimated_cost: float,
        actual_cost: float,
    ) -> None:
        """Reconcile a reserved budget slot with the actual cost.

        Adds ``actual_cost - estimated_cost`` to ``_spent``
        under the lock.  The delta may be negative (cheaper than
        expected) or positive (more expensive).  Call this from the
        ``finally`` of an LLM invocation that was previously gated
        via :meth:`try_reserve`.

        Args:
            estimated_cost: The cost that was reserved.
            actual_cost: The actual cost observed.

        Raises:
            ValueError: If ``actual_cost`` is negative.
        """
        if not math.isfinite(estimated_cost) or estimated_cost < 0:
            msg = "estimated_cost must be a finite non-negative number"
            logger.warning(
                INVALID_COST,
                cost=estimated_cost,
                kind="estimated",
                reason=msg,
            )
            raise ValueError(msg)
        if not math.isfinite(actual_cost) or actual_cost < 0:
            msg = "actual_cost must be a finite non-negative number"
            logger.warning(
                INVALID_COST,
                cost=actual_cost,
                kind="actual",
                reason=msg,
            )
            raise ValueError(msg)
        delta = actual_cost - estimated_cost
        async with self._lock:
            self._spent += delta
            logger.info(
                DETECTOR_COST_INCURRED,
                cost=actual_cost,
                estimated_cost=estimated_cost,
                delta=delta,
                total_spent=self._spent,
                remaining=max(0.0, self._budget - self._spent),
            )

    async def release(self, estimated_cost: float) -> None:
        """Release a previously-reserved slot that never incurred cost.

        Use this when ``try_reserve`` succeeded but the provider
        call failed before any cost was incurred (e.g. rate-limiter
        acquisition error, immediate connection failure), so the
        reserved estimate should be refunded.

        Args:
            estimated_cost: The cost that was reserved and is being
                released.  Must be non-negative.

        Raises:
            ValueError: If ``estimated_cost`` is negative.
        """
        if not math.isfinite(estimated_cost) or estimated_cost < 0:
            msg = "estimated_cost must be a finite non-negative number"
            logger.warning(
                INVALID_COST,
                cost=estimated_cost,
                kind="release",
                reason=msg,
            )
            raise ValueError(msg)
        async with self._lock:
            refunded = min(estimated_cost, self._spent)
            self._spent = max(0.0, self._spent - estimated_cost)
            # Refund is a state transition (spend goes down). Log at
            # INFO with a negative ``delta`` to mirror the ``settle``
            # emission shape so aggregators can reconcile reserve +
            # release pairs without special-casing the event name.
            logger.info(
                DETECTOR_COST_INCURRED,
                cost=0.0,
                estimated_cost=estimated_cost,
                delta=-refunded,
                total_spent=self._spent,
                remaining=max(0.0, self._budget - self._spent),
            )

    @property
    def remaining(self) -> float:
        """Remaining budget in the configured currency.

        Returns an advisory snapshot -- concurrent callers may see
        stale values.  Use :meth:`try_reserve` for atomic admission.
        """
        return max(0.0, self._budget - self._spent)

    @property
    def total_spent(self) -> float:
        """Total spent so far in the configured currency (advisory snapshot)."""
        return self._spent
