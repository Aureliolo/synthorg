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
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from synthorg.observability import get_logger

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


class IdempotencyService:
    """Lifecycle wrapper around :class:`IdempotencyRepository`."""

    def __init__(
        self,
        repository: IdempotencyRepository,
        *,
        ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    ) -> None:
        self._repo = repository
        self._ttl_seconds = ttl_seconds

    async def run_idempotent(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        callback: Callable[[], Awaitable[Any]],
    ) -> tuple[Any, bool]:
        """Run *callback* exactly once for ``(scope, key)``.

        Returns ``(result, fresh)``. ``fresh=True`` means the
        callback executed in this call; ``fresh=False`` means the
        cached prior result was returned.

        On ``IN_FLIGHT``: poll with exponential backoff up to
        :data:`_IN_FLIGHT_POLL_TIMEOUT_SECONDS`, then give up. Caller
        chooses whether to surface 409 or wait further.

        On callback exception: mark the key as FAILED so the next
        retry can re-claim, and re-raise the original exception.
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
            return cached, False

        if claim.outcome is IdempotencyOutcome.IN_FLIGHT:
            logger.info(
                IDEMPOTENCY_CLAIM_IN_FLIGHT,
                scope=scope,
                key=key,
            )
            cached = await self._wait_for_in_flight(scope=scope, key=key)
            if cached is not None:
                return cached, False
            # Polling timed out -- caller will receive None and is
            # expected to translate to 409 Conflict at the API layer.
            return None, False

        # FRESH -- execute the callback under the claim.
        logger.info(IDEMPOTENCY_CLAIM_FRESH, scope=scope, key=key)
        try:
            result = await callback()
        except Exception:
            await self._mark_failed_safely(scope=scope, key=key)
            raise

        await self._record_completion(
            scope=scope,
            key=key,
            result=result,
        )
        return result, True

    async def _wait_for_in_flight(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> Any | None:
        """Poll until the in-flight claim resolves or timeout.

        Uses ``time.monotonic`` rather than wall-clock arithmetic so a
        clock skew, NTP adjustment, or VM suspend/resume cannot extend
        or short-circuit the polling deadline.
        """
        deadline = time.monotonic() + _IN_FLIGHT_POLL_TIMEOUT_SECONDS
        backoff = _IN_FLIGHT_POLL_INITIAL_BACKOFF_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _IN_FLIGHT_POLL_MAX_BACKOFF_SECONDS)
            record = await self._repo.get(scope=scope, key=key)
            if record is None:
                return None
            if record.status is IdempotencyOutcome.COMPLETED:
                logger.info(
                    IDEMPOTENCY_CLAIM_COMPLETED,
                    scope=scope,
                    key=key,
                    note="resolved_after_in_flight_poll",
                )
                if record.response_body:
                    return json.loads(record.response_body)
                return None
            if record.status is IdempotencyOutcome.FAILED:
                logger.warning(
                    IDEMPOTENCY_CLAIM_FAILED_REPLAY,
                    scope=scope,
                    key=key,
                )
                return None
        return None

    async def _record_completion(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
        result: Any,
    ) -> None:
        body = json.dumps(result, default=str, sort_keys=True)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        await self._repo.complete(
            scope=scope,
            key=key,
            response_body=body,
            response_hash=digest,
        )
        logger.info(IDEMPOTENCY_COMPLETE, scope=scope, key=key)

    async def _mark_failed_safely(
        self,
        *,
        scope: NotBlankStr,
        key: NotBlankStr,
    ) -> None:
        try:
            await self._repo.fail(scope=scope, key=key)
            logger.info(IDEMPOTENCY_FAIL, scope=scope, key=key)
        except Exception as exc:
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
            )

    async def cleanup_expired(self) -> int:
        """Reap expired rows. Caller schedules the periodic invocation."""
        removed = await self._repo.cleanup_expired(datetime.now(UTC))
        if removed:
            logger.info(IDEMPOTENCY_CLEANUP, removed=removed)
        return removed
