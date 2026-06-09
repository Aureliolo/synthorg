"""Idempotency service -- claim/complete/fail wrapper with response caching.

Wraps :class:`IdempotencyRepository` so controllers do not have to
hand-roll the lifecycle. The primitive method is
:meth:`run_idempotent`, which serialises a callback's response as
JSON and stores it for the configured TTL so duplicate callers
receive the original reply rather than a 409.

Default TTL is 24 hours (matches Stripe-style retry windows).
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from synthorg.core.types import NotBlankStr
from synthorg.observability.events.idempotency import (
    IDEMPOTENCY_CLAIM_COMPLETED,
    IDEMPOTENCY_CLAIM_FAILED_REPLAY,
    IDEMPOTENCY_CLAIM_FRESH,
    IDEMPOTENCY_CLAIM_IN_FLIGHT,
    IDEMPOTENCY_CLEANUP,
    IDEMPOTENCY_COMPLETE,
    IDEMPOTENCY_FAIL,
)
from synthorg.persistence.idempotency_protocol import (
    IdempotencyOutcome,
    IdempotencyRepository,
)

logger = get_logger(__name__)

#: Default TTL: 24 hours (matches Stripe-style retry windows).
DEFAULT_IDEMPOTENCY_TTL_SECONDS: int = 24 * 60 * 60

#: Maximum total wait when polling for an in-flight claim to complete.
_IN_FLIGHT_POLL_TIMEOUT_SECONDS: float = 30.0
_IN_FLIGHT_POLL_INITIAL_BACKOFF_SECONDS: float = 0.05
_IN_FLIGHT_POLL_MAX_BACKOFF_SECONDS: float = 1.0

#: Maximum number of full claim retries triggered by a leader-failed
#: outcome.  In practice the second attempt either lands FRESH (the
#: row's lease was rotated by ``claim()``) or sees a new IN_FLIGHT
#: leader and falls back to the polling/timeout path -- so capping at
#: two protects against pathological churn while preserving the
#: single retry that the redelivery contract calls for.
_MAX_LEADER_FAILED_RETRIES: int = 1


class _PollOutcome(StrEnum):
    """Discriminator for ``_wait_for_in_flight`` results.

    ``COMPLETED`` -- a previous in-flight winner finished successfully
    and the cached response body is in ``body``.

    ``LEADER_FAILED`` -- the previous in-flight winner errored out and
    flipped the row to FAILED.  ``run_idempotent`` re-claims so the
    next caller can retry the work, rather than 409'ing the retry
    away.

    ``TIMED_OUT`` -- polling exhausted its budget without seeing the
    leader resolve.  Caller surfaces this as 409 Conflict to keep the
    semantics of "still in flight, try again later".
    """

    COMPLETED = "completed"
    LEADER_FAILED = "leader_failed"
    TIMED_OUT = "timed_out"


#: Internal sentinel value returned by ``_run_idempotent_once`` to tell
#: the outer ``run_idempotent`` retry loop "the in-flight leader
#: failed; re-claim".  Distinct from ``None`` so a callback that
#: legitimately returns ``None`` cannot be confused with the
#: leader-failed signal.
_LeaderFailedSentinel: object = object()


@dataclass(frozen=True, slots=True)
class IdempotencyResult:
    """Disambiguated outcome of :meth:`IdempotencyService.run_idempotent`.

    A bare ``(result, fresh)`` tuple cannot tell three states apart --
    a callback that legitimately returned ``None``, a polling timeout
    on an in-flight claim, and a leader-failed-and-retry-exhausted --
    all of which would otherwise surface as ``cached is None`` at call
    sites and translate to 409 Conflict regardless of whether the
    cached body was a real ``null``. A discriminated wrapper forces
    callers to handle each case explicitly.

    Attributes:
        result: The callback's (or cached) return value. May be
            ``None`` for callbacks that legitimately return ``None``.
            Treat as authoritative ONLY when ``timed_out`` is
            ``False``; when ``timed_out`` is ``True`` the field is
            always ``None`` and the caller must surface 409 / retry.
        fresh: ``True`` when this call executed the callback;
            ``False`` when it returned a cached prior result OR when
            it timed out.
        timed_out: ``True`` when the in-flight poll expired without
            seeing the leader resolve, OR when leader-failed retries
            were exhausted. The caller surfaces this as a 409
            Conflict; the row in the repository may eventually
            cleanup to FAILED via ``cleanup_expired``.
    """

    result: object
    fresh: bool
    timed_out: bool


class IdempotencyService:
    """Lifecycle wrapper around :class:`IdempotencyRepository`."""

    def __init__(
        self,
        repository: IdempotencyRepository,
        *,
        ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        # Invariant: the configured TTL must outlive a polling cycle.
        # The leader-failed takeover path in ``_wait_for_in_flight``
        # treats a missing claim row as ``LEADER_FAILED`` (the leader
        # has gone, so re-claim is safe). That is only sound if a
        # legitimately in-flight claim cannot have its row deleted /
        # expired during the poll window: otherwise a still-running
        # leader could be observed as missing and a follower would
        # re-execute the callback concurrently. Enforcing
        # ``ttl_seconds > _IN_FLIGHT_POLL_TIMEOUT_SECONDS`` at
        # construction makes the invariant load-bearing instead of
        # a runtime-time-of-check race.
        if ttl_seconds <= _IN_FLIGHT_POLL_TIMEOUT_SECONDS:
            msg = (
                f"ttl_seconds={ttl_seconds} must exceed "
                f"_IN_FLIGHT_POLL_TIMEOUT_SECONDS="
                f"{_IN_FLIGHT_POLL_TIMEOUT_SECONDS} so a still-running "
                "leader cannot be observed as a missing row mid-poll."
            )
            raise ValueError(msg)
        self._repo = repository
        self._ttl_seconds = ttl_seconds
        self._clock = clock or SystemClock()

    async def run_idempotent(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        callback: Callable[[], Awaitable[object]],
    ) -> IdempotencyResult:
        """Run *callback* exactly once for ``(scope, key)``.

        Returns an :class:`IdempotencyResult` discriminated wrapper
        so callers can distinguish a legitimate ``None`` callback
        return from an in-flight timeout. Inspect ``timed_out``
        first; only trust ``result`` when ``timed_out`` is ``False``.

        On ``IN_FLIGHT``: poll with exponential backoff up to
        :data:`_IN_FLIGHT_POLL_TIMEOUT_SECONDS`, then give up. The
        wrapper surfaces ``timed_out=True`` so the caller can 409.

        On callback exception: mark the key as FAILED so the next
        retry can re-claim, and re-raise the original exception.

        Leader-failed handling: if the in-flight leader fails while
        we are polling, ``_wait_for_in_flight`` returns
        ``LEADER_FAILED`` and we re-loop into ``claim()`` so this
        caller can take over the work rather than receiving a 409
        for an attempt that never actually published. Capped at
        :data:`_MAX_LEADER_FAILED_RETRIES` to bound the worst-case
        churn under sustained leader failures.

        Returns:
            ``IdempotencyResult`` instance.
        """
        retries_after_leader_failure = 0
        # lint-allow: long-running-loop-kill-switch -- per-request retry-wait.
        while True:
            outcome_value, fresh, timed_out = await self._run_idempotent_once(
                scope=scope,
                key=key,
                callback=callback,
            )
            if outcome_value is not _LeaderFailedSentinel:
                return IdempotencyResult(
                    result=outcome_value,
                    fresh=fresh,
                    timed_out=timed_out,
                )
            if retries_after_leader_failure >= _MAX_LEADER_FAILED_RETRIES:
                # Repeated leader failures look like a sustained
                # downstream outage; surface ``timed_out=True`` so
                # the caller can back off / 409 rather than spinning.
                return IdempotencyResult(result=None, fresh=False, timed_out=True)
            retries_after_leader_failure += 1

    async def _run_idempotent_once(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        callback: Callable[[], Awaitable[object]],
    ) -> tuple[object, bool, bool]:
        """Single attempt of the claim/run cycle.

        Returns ``(result, fresh, timed_out)``. The first element is
        :data:`_LeaderFailedSentinel` to tell the caller to re-claim
        because the prior in-flight leader flipped the row to
        ``FAILED``; any other value is the canonical callback result.
        ``timed_out`` is ``True`` when the in-flight poll exhausted
        its budget without observing a final state.

        Returns:
            Tuple of the declared element types.

        Raises:
            ValueError: Raised on the corresponding failure path.
            MemoryError: Raised on the corresponding failure path.
            RecursionError: Raised on the corresponding failure path.
        """
        now = datetime.now(UTC)
        claim = await self._repo.claim(
            scope=scope,
            key=key,
            ttl_seconds=self._ttl_seconds,
            now=now,
        )

        if claim.outcome is IdempotencyOutcome.COMPLETED:
            logger.info(
                IDEMPOTENCY_CLAIM_COMPLETED,
                scope=scope,
                key=key,
            )
            cached = (
                json.loads(claim.cached_response) if claim.cached_response else None
            )
            return cached, False, False

        if claim.outcome is IdempotencyOutcome.IN_FLIGHT:
            logger.info(
                IDEMPOTENCY_CLAIM_IN_FLIGHT,
                scope=scope,
                key=key,
            )
            poll_outcome, body = await self._wait_for_in_flight(
                scope=scope,
                key=key,
            )
            if poll_outcome is _PollOutcome.COMPLETED:
                return body, False, False
            if poll_outcome is _PollOutcome.LEADER_FAILED:
                # Tell the caller to re-claim. The repo's claim() has
                # already rotated the lease for the FAILED row so the
                # next call lands FRESH.
                return _LeaderFailedSentinel, False, False
            # TIMED_OUT -- caller surfaces 409 Conflict.
            return None, False, True

        # FRESH -- execute the callback under the claim. The
        # ``claim_token`` is the lease this worker owns; ``complete``
        # / ``fail`` enforce token equality so a slow worker that
        # ran past the in-flight window cannot CAS-overwrite a row
        # the rotation handed to a fresh worker. The
        # ``IdempotencyClaim`` model_validator already enforces that
        # FRESH outcomes carry a non-None token, so this lookup is
        # type-safe; we surface a defensive ValueError to the
        # operator instead of an untyped AttributeError if the
        # invariant ever regressed.
        token = claim.claim_token
        if token is None:
            msg = "FRESH claim must carry a claim_token"
            raise ValueError(msg)
        logger.info(IDEMPOTENCY_CLAIM_FRESH, scope=scope, key=key)
        try:
            result = await callback()
        except MemoryError, RecursionError:
            # System errors must propagate immediately; do not touch
            # the claim row. Project convention.
            raise
        except Exception:
            await self._mark_failed_safely(
                scope=scope,
                key=key,
                claim_token=token,
            )
            raise

        body = await self._record_completion(
            scope=scope,
            key=key,
            result=result,
            claim_token=token,
        )
        # Round-trip through ``json.loads`` so the fresh-path return
        # value has the same shape as the replay-path return value
        # (which is always ``json.loads(body)``). Without this, a fresh
        # caller would receive ``dict[str, object]`` while a replay
        # caller would receive whatever JSON-decoding produces, leaving
        # callers with type-unstable behaviour they cannot reason about.
        return json.loads(body), True, False

    async def _wait_for_in_flight(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> tuple[_PollOutcome, object | None]:
        """Poll until the in-flight claim resolves or timeout.

        Uses ``time.monotonic`` rather than wall-clock arithmetic so a
        clock skew, NTP adjustment, or VM suspend/resume cannot extend
        or short-circuit the polling deadline.

        Returns ``(_PollOutcome, body)`` so the caller can distinguish
        a successful in-flight resolution (``COMPLETED`` + cached
        body) from a leader failure (``LEADER_FAILED``, body always
        ``None``) and from polling exhaustion (``TIMED_OUT``, body
        always ``None``). Conflating leader-failure and timeout into
        a single ``None`` would 409 every retry after a failed
        leader, defeating redelivery semantics.

        Returns:
            ``tuple[_PollOutcome, object | None]``; always a tuple, with
            the second element (cached body) possibly ``None``.
        """
        deadline = self._clock.monotonic() + _IN_FLIGHT_POLL_TIMEOUT_SECONDS
        backoff = _IN_FLIGHT_POLL_INITIAL_BACKOFF_SECONDS
        while self._clock.monotonic() < deadline:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _IN_FLIGHT_POLL_MAX_BACKOFF_SECONDS)
            record = await self._repo.get(scope=scope, key=key)
            if record is None:
                # The leader's row was deleted (cleanup / TTL expiry).
                # Treat as leader-failed so the caller re-claims and
                # takes over rather than 409'ing.
                return _PollOutcome.LEADER_FAILED, None
            if record.status is IdempotencyOutcome.COMPLETED:
                logger.info(
                    IDEMPOTENCY_CLAIM_COMPLETED,
                    scope=scope,
                    key=key,
                    note="resolved_after_in_flight_poll",
                )
                body = (
                    json.loads(record.response_body) if record.response_body else None
                )
                return _PollOutcome.COMPLETED, body
            if record.status is IdempotencyOutcome.FAILED:
                logger.warning(
                    IDEMPOTENCY_CLAIM_FAILED_REPLAY,
                    scope=scope,
                    key=key,
                )
                return _PollOutcome.LEADER_FAILED, None
        return _PollOutcome.TIMED_OUT, None

    async def _record_completion(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        result: object,
        claim_token: NotBlankStr,
    ) -> str:
        """Persist *result* as the cached response body and return it.

        Callbacks must return a JSON-serialisable value: the strict
        ``json.dumps`` (no ``default=str`` fallback) raises
        ``TypeError`` for non-serialisable types so a controller cannot
        silently cache a string-coerced object that round-trips back
        to the caller as a different type. The caller round-trips the
        returned body through ``json.loads`` so fresh and replay paths
        return identical shapes.

        ``claim_token`` is the lease the FRESH winner received. The
        repository's ``complete`` enforces token equality so a stale
        worker that finished after the lease rotated cannot overwrite
        the new lease's row.

        Returns:
            Resulting string.
        """
        body = json.dumps(result, sort_keys=True)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        committed = await self._repo.complete(
            scope=scope,
            key=key,
            response_body=body,
            response_hash=digest,
            claim_token=claim_token,
        )
        if not committed:
            # Lease rotated while we were running -- do NOT silently
            # ignore. The caller still gets the intended return
            # value but operators see the rotation in the log.
            logger.warning(
                IDEMPOTENCY_COMPLETE,
                scope=scope,
                key=key,
                note="claim_token_rotated_skipping_completion",
            )
        else:
            logger.info(IDEMPOTENCY_COMPLETE, scope=scope, key=key)
        return body

    async def _mark_failed_safely(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        claim_token: NotBlankStr,
    ) -> None:
        """Best-effort transition of the claimed row to FAILED.

        The original callback exception is what the caller propagates;
        a failure to record the FAILED state here is logged at WARNING
        and swallowed so the row's ``expires_at`` drives eventual
        cleanup instead of masking the caller's error.
        """
        try:
            committed = await self._repo.fail(
                scope=scope,
                key=key,
                claim_token=claim_token,
            )
            if not committed:
                logger.warning(
                    IDEMPOTENCY_FAIL,
                    scope=scope,
                    key=key,
                    note="claim_token_rotated_skipping_fail",
                )
            else:
                logger.info(IDEMPOTENCY_FAIL, scope=scope, key=key)
        except Exception as exc:
            reraise_critical(exc)
            # The original callback exception is the one the caller
            # cares about; failing to mark the row failed is best-
            # effort. Log at WARNING and let the row's expires_at
            # handle eventual cleanup.
            logger.warning(
                IDEMPOTENCY_FAIL,
                scope=scope,
                key=key,
                note="fail_marker_persistence_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def cleanup_expired(self) -> int:
        """Reap expired rows. Caller schedules the periodic invocation.

        Returns:
            Resulting integer.
        """
        removed = await self._repo.cleanup_expired(datetime.now(UTC))
        if removed:
            logger.info(IDEMPOTENCY_CLEANUP, removed=removed)
        return removed
