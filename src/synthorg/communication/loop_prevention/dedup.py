"""Delegation deduplication within a time window."""

import time
from collections.abc import Callable  # noqa: TC003

from synthorg.communication.loop_prevention.models import GuardCheckOutcome
from synthorg.observability import get_logger
from synthorg.observability.events.delegation import (
    DELEGATION_LOOP_DEDUP_BLOCKED,
)

logger = get_logger(__name__)

_MECHANISM = "dedup"


class DelegationDeduplicator:
    """Rejects identical delegations within a time window.

    Identity is determined by the directional tuple
    ``(delegator_id, delegatee_id, task_id)`` — the unique task ID is
    used instead of the title so that different tasks with the same
    title are not falsely blocked, and refined re-delegations of the
    same task are correctly deduplicated.

    Expired entries are pruned globally on every ``check`` and
    ``record`` call.

    Args:
        window_seconds: Duration of the dedup window.
        clock: Monotonic clock function for deterministic testing.
    """

    __slots__ = ("_clock", "_records", "_window_seconds")

    def __init__(
        self,
        window_seconds: int = 60,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window_seconds = window_seconds
        self._clock = clock
        self._records: dict[tuple[str, str, str], float] = {}

    def _purge_expired(self) -> None:
        """Remove all globally expired entries."""
        now = self._clock()
        cutoff = now - self._window_seconds
        expired = [k for k, ts in self._records.items() if ts <= cutoff]
        for k in expired:
            del self._records[k]

    def check(
        self,
        delegator_id: str,
        delegatee_id: str,
        task_id: str,
    ) -> GuardCheckOutcome:
        """Check for duplicate delegation within the window.

        Args:
            delegator_id: ID of the delegating agent.
            delegatee_id: ID of the target agent.
            task_id: Unique ID of the task being delegated.

        Returns:
            Outcome with passed=False if a duplicate exists.
        """
        self._purge_expired()
        # Directional key: A->B and B->A are distinct delegations
        key = (delegator_id, delegatee_id, task_id)
        recorded_at = self._records.get(key)
        if recorded_at is not None:
            elapsed = self._clock() - recorded_at
            logger.info(
                DELEGATION_LOOP_DEDUP_BLOCKED,
                delegator=delegator_id,
                delegatee=delegatee_id,
                task_id=task_id,
                elapsed=elapsed,
                window=self._window_seconds,
            )
            return GuardCheckOutcome(
                passed=False,
                mechanism=_MECHANISM,
                message=(
                    f"Duplicate delegation "
                    f"({delegator_id!r} -> {delegatee_id!r}, "
                    f"{task_id!r}) within "
                    f"{self._window_seconds}s window"
                ),
            )
        return GuardCheckOutcome(passed=True, mechanism=_MECHANISM)

    def record(
        self,
        delegator_id: str,
        delegatee_id: str,
        task_id: str,
    ) -> None:
        """Record a delegation for future dedup checks.

        Args:
            delegator_id: ID of the delegating agent.
            delegatee_id: ID of the target agent.
            task_id: Unique ID of the task being delegated.
        """
        self._purge_expired()
        key = (delegator_id, delegatee_id, task_id)
        self._records[key] = self._clock()
