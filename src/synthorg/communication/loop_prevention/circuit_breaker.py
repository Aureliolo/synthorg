"""Circuit breaker for delegation bounces between agent pairs."""

import threading
import time
from collections.abc import Callable  # noqa: TC003
from enum import StrEnum

from synthorg.communication.config import CircuitBreakerConfig  # noqa: TC001
from synthorg.communication.loop_prevention._pair_key import pair_key
from synthorg.communication.loop_prevention.models import GuardCheckOutcome
from synthorg.observability import get_logger
from synthorg.observability.events.delegation import (
    DELEGATION_LOOP_CIRCUIT_BACKOFF,
    DELEGATION_LOOP_CIRCUIT_OPEN,
    DELEGATION_LOOP_CIRCUIT_PERSIST_FAILED,
    DELEGATION_LOOP_CIRCUIT_RESET,
)
from synthorg.persistence.circuit_breaker_repo import (
    CircuitBreakerStateRecord,
    CircuitBreakerStateRepository,
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
        trip_count: Number of times the circuit has tripped.
    """

    __slots__ = ("bounce_count", "opened_at", "trip_count")

    def __init__(self) -> None:
        self.bounce_count: int = 0
        self.opened_at: float | None = None
        self.trip_count: int = 0


class DelegationCircuitBreaker:
    """Tracks delegation bounces per sorted agent pair.

    After ``bounce_threshold`` bounces between the same pair, the
    circuit opens for a cooldown period that grows exponentially
    with each successive trip (capped at ``max_cooldown_seconds``).

    Args:
        config: Circuit breaker configuration.
        clock: Monotonic clock function for deterministic testing.
        state_repo: Optional persistence repository for surviving
            restarts.
    """

    __slots__ = (
        "_clock",
        "_config",
        "_dirty",
        "_pairs",
        "_state_lock",
        "_state_repo",
    )

    def __init__(
        self,
        config: CircuitBreakerConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        state_repo: CircuitBreakerStateRepository | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._state_repo = state_repo
        self._pairs: dict[tuple[str, str], _PairState] = {}
        self._dirty: set[tuple[str, str]] = set()
        # Sync RLock guards _pairs + _dirty mutations.  The breaker is
        # called from sync code paths inside async tasks; a
        # threading.RLock works in both contexts (pure Python Lock
        # would re-enter and deadlock if get_state calls the same
        # locked region indirectly via the repo).
        self._state_lock = threading.RLock()

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

    def _compute_cooldown(self, trip_count: int) -> float:
        """Compute cooldown with exponential backoff, capped at max.

        Formula: ``base * 2^(trip_count - 1)`` for ``trip_count >= 1``.
        First trip uses the base cooldown unchanged.

        Args:
            trip_count: Number of times the circuit has tripped.

        Returns:
            Cooldown period in seconds.
        """
        if trip_count <= 0:
            return float(self._config.cooldown_seconds)
        # Cap exponent at 63 so corrupted/huge trip_count values cannot
        # trigger expensive big-int math.  min() caps the result anyway.
        exponent = min(trip_count - 1, 63)
        backoff = self._config.cooldown_seconds * (2**exponent)
        return min(float(backoff), float(self._config.max_cooldown_seconds))

    def get_state(
        self,
        delegator_id: str,
        delegatee_id: str,
    ) -> CircuitBreakerState:
        """Get the circuit breaker state for an agent pair.

        If the circuit was previously open and the cooldown has expired,
        the bounce count is reset (but trip history is preserved) before
        returning ``CLOSED``.

        Args:
            delegator_id: First agent ID.
            delegatee_id: Second agent ID.

        Returns:
            Current state of the circuit breaker.
        """
        with self._state_lock:
            pair = self._get_pair(delegator_id, delegatee_id)
            if pair is None:
                return CircuitBreakerState.CLOSED
            if pair.opened_at is not None:
                elapsed = self._clock() - pair.opened_at
                cooldown = self._compute_cooldown(pair.trip_count)
                if elapsed < cooldown:
                    return CircuitBreakerState.OPEN
                # Cooldown expired: reset bounce count, preserve trip history
                key = pair_key(delegator_id, delegatee_id)
                pair.bounce_count = 0
                pair.opened_at = None
                self._dirty.add(key)
                logger.info(
                    DELEGATION_LOOP_CIRCUIT_RESET,
                    delegator=delegator_id,
                    delegatee=delegatee_id,
                    cooldown_seconds=cooldown,
                    trip_count=pair.trip_count,
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
        # Hold the lock across both the state evaluation and the
        # cooldown read. Splitting them lets a concurrent
        # ``record_delegation`` reset or mutate the pair between
        # ``get_state`` and the post-hoc ``_get_pair`` lookup, which
        # would surface as a stale cooldown value or a missing pair
        # in the OPEN branch.
        with self._state_lock:
            pair = self._get_pair(delegator_id, delegatee_id)
            if pair is None or pair.opened_at is None:
                return GuardCheckOutcome(passed=True, mechanism=_MECHANISM)
            elapsed = self._clock() - pair.opened_at
            cooldown = self._compute_cooldown(pair.trip_count)
            if elapsed >= cooldown:
                key = pair_key(delegator_id, delegatee_id)
                pair.bounce_count = 0
                pair.opened_at = None
                self._dirty.add(key)
                logger.info(
                    DELEGATION_LOOP_CIRCUIT_RESET,
                    delegator=delegator_id,
                    delegatee=delegatee_id,
                    cooldown_seconds=cooldown,
                    trip_count=pair.trip_count,
                )
                return GuardCheckOutcome(passed=True, mechanism=_MECHANISM)
        logger.info(
            DELEGATION_LOOP_CIRCUIT_OPEN,
            delegator=delegator_id,
            delegatee=delegatee_id,
            cooldown_seconds=cooldown,
        )
        return GuardCheckOutcome(
            passed=False,
            mechanism=_MECHANISM,
            message=(
                f"Circuit breaker open for pair "
                f"({delegator_id!r}, {delegatee_id!r}); "
                f"cooldown {cooldown}s"
            ),
        )

    def record_delegation(
        self,
        delegator_id: str,
        delegatee_id: str,
    ) -> None:
        """Record a delegation event for the pair.

        Each delegation between a pair increments the bounce counter.
        Back-and-forth patterns trip the breaker fastest because the
        key is direction-agnostic.  If the count reaches the threshold,
        the circuit opens and ``trip_count`` is incremented.  If the
        circuit is already open (cooldown not yet expired), this call
        is a no-op.

        Args:
            delegator_id: First agent ID.
            delegatee_id: Second agent ID.
        """
        # Single critical section: the OPEN-state check, the bounce
        # increment, and the threshold transition all run under the
        # same lock so two concurrent callers cannot both observe
        # CLOSED, both bump ``trip_count`` / ``opened_at``, and skip
        # backoff levels.
        with self._state_lock:
            pair = self._get_or_create_pair(delegator_id, delegatee_id)
            if pair.opened_at is not None:
                elapsed = self._clock() - pair.opened_at
                cooldown = self._compute_cooldown(pair.trip_count)
                if elapsed < cooldown:
                    return
                # Cooldown expired between calls -- reset bounce state
                # under the same lock so the bump below counts toward
                # a fresh post-cooldown window.
                key = pair_key(delegator_id, delegatee_id)
                pair.bounce_count = 0
                pair.opened_at = None
                self._dirty.add(key)
                logger.info(
                    DELEGATION_LOOP_CIRCUIT_RESET,
                    delegator=delegator_id,
                    delegatee=delegatee_id,
                    cooldown_seconds=cooldown,
                    trip_count=pair.trip_count,
                )
            pair.bounce_count += 1
            if pair.bounce_count >= self._config.bounce_threshold:
                pair.trip_count += 1
                pair.opened_at = self._clock()
                cooldown = self._compute_cooldown(pair.trip_count)
                key = pair_key(delegator_id, delegatee_id)
                self._dirty.add(key)
                logger.warning(
                    DELEGATION_LOOP_CIRCUIT_BACKOFF,
                    delegator=delegator_id,
                    delegatee=delegatee_id,
                    bounce_count=pair.bounce_count,
                    threshold=self._config.bounce_threshold,
                    trip_count=pair.trip_count,
                    cooldown_seconds=cooldown,
                )

    # Persistence helpers (async, called outside hot path)

    async def load_state(self) -> None:
        """Load persisted circuit breaker state from the repository.

        Called once at startup to restore state across restarts.
        No-op if no repository is configured.  On failure, logs the
        error and re-raises so callers can decide whether to proceed
        with empty state or abort.

        Raises:
            Exception: If the repository fails to load state.
        """
        if self._state_repo is None:
            return
        try:
            records = await self._state_repo.load_all()
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                DELEGATION_LOOP_CIRCUIT_PERSIST_FAILED,
                note="load_state failed; circuit breaker starting with empty state",
            )
            raise
        # Hot-path may already be running by the time persistence
        # finishes; take the lock for the bulk install so a concurrent
        # ``record_delegation`` cannot observe a half-restored
        # ``_pairs`` dict mid-iteration.  Use ``setdefault`` so newer
        # in-memory state created by ``record_delegation`` between
        # process start and ``load_state`` completing is not silently
        # overwritten by the persisted snapshot.
        with self._state_lock:
            for rec in records:
                key = (rec.pair_key_a, rec.pair_key_b)
                ps = _PairState()
                ps.bounce_count = rec.bounce_count
                ps.trip_count = rec.trip_count
                # ``opened_at`` is a monotonic value captured by the
                # original process; another process's monotonic
                # reference point is undefined so a persisted value
                # cannot be safely compared against a fresh
                # ``self._clock()`` call.  Drop ``opened_at`` on
                # restore so the breaker re-opens cleanly under the
                # current process's clock the next time
                # ``record_delegation`` trips it; the trip-count
                # history is preserved (so backoff escalation
                # survives), only the in-flight cooldown is reset.
                ps.opened_at = None
                self._pairs.setdefault(key, ps)

    async def persist_dirty(self) -> None:
        """Flush dirty pair state to the repository.

        Best-effort: errors are logged and swallowed per pair.
        No-op if no repository is configured.
        """
        if self._state_repo is None:
            with self._state_lock:
                self._dirty.clear()
            return

        # Snapshot dirty keys + their pair state under the lock so a
        # concurrent ``record_delegation`` cannot mutate a pair after
        # the snapshot but before the save records what was observed.
        # The save itself runs unlocked (I/O), and the dirty discard
        # only fires when the snapshot value still matches the
        # currently-cached state (no newer in-memory update has
        # arrived in the meantime).
        with self._state_lock:
            dirty = tuple(self._dirty)
            snapshot: dict[
                tuple[str, str],
                tuple[int, int, float | None],
            ] = {}
            for key in dirty:
                pair = self._pairs.get(key)
                if pair is None:
                    self._dirty.discard(key)
                    continue
                snapshot[key] = (
                    pair.bounce_count,
                    pair.trip_count,
                    pair.opened_at,
                )

        for key, (bounce, trip, opened) in snapshot.items():
            try:
                record = CircuitBreakerStateRecord(
                    pair_key_a=key[0],
                    pair_key_b=key[1],
                    bounce_count=bounce,
                    trip_count=trip,
                    opened_at=opened,
                )
                await self._state_repo.save(record)
            except MemoryError, RecursionError:
                raise
            except Exception:
                # Key stays in _dirty for retry on next persist cycle.
                logger.exception(
                    DELEGATION_LOOP_CIRCUIT_PERSIST_FAILED,
                    delegator=key[0],
                    delegatee=key[1],
                )
                continue
            with self._state_lock:
                # Only clear the dirty marker if the cached pair has
                # not been updated since we snapshotted it. A newer
                # update would otherwise lose its dirty state and the
                # next persist cycle would skip it.
                live = self._pairs.get(key)
                if live is not None and (
                    live.bounce_count == bounce
                    and live.trip_count == trip
                    and live.opened_at == opened
                ):
                    self._dirty.discard(key)
