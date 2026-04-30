"""Webhook replay protection.

Prevents replay attacks by tracking nonces and validating
timestamps within a configurable window.
"""

import hashlib
import math
from collections import OrderedDict

from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import WEBHOOK_REPLAY_DETECTED

logger = get_logger(__name__)

_DEFAULT_WINDOW_SECONDS = 300
_DEFAULT_MAX_ENTRIES = 10_000
# Attacker-controlled nonces are hashed to a fixed 32-byte digest
# before being stored in ``_seen`` so the cache's per-entry memory
# is bounded regardless of how long the incoming header is.
# Reject nonces larger than ``MAX_NONCE_CHARS`` outright -- even
# the hash computation is cheap but O(n), and any legitimate
# webhook provider ships nonces well under this limit. Re-exported
# so the durable idempotency path in ``api/controllers/webhooks.py``
# can apply the same guard before composing its DB key.
MAX_NONCE_CHARS: int = 1024


def _fingerprint_nonce(nonce: str) -> str:
    """Return a fixed-size cache key for a nonce.

    Uses SHA-256 so two different nonces cannot collide in the
    replay cache, and the stored key size is bounded independent
    of the attacker-supplied input length.
    """
    return hashlib.sha256(nonce.encode("utf-8", errors="replace")).hexdigest()


class ReplayProtector:
    """In-memory nonce + timestamp replay protection.

    Rejects requests with:
    - A timestamp outside the configured window.
    - A previously-seen nonce within the window.

    Nonces are evicted when they expire beyond the window. The
    store is also bounded: once ``max_entries`` is reached, the
    oldest nonces are dropped in insertion order to prevent an
    attacker from exhausting memory with unique nonces.

    Args:
        window_seconds: Maximum clock skew / replay window.
        max_entries: Maximum nonces retained at once.
        clock: Time source (injectable for deterministic tests).
            Wall-clock epoch values are read via
            ``clock.now().timestamp()`` so the freshness check
            compares like-for-like with the attacker-supplied
            timestamp header.
    """

    def __init__(
        self,
        window_seconds: int = _DEFAULT_WINDOW_SECONDS,
        *,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        clock: Clock | None = None,
    ) -> None:
        # Validate up-front so a config typo cannot silently disable
        # replay protection. ``max_entries <= 0`` would evict every
        # accepted nonce immediately; ``window_seconds <= 0`` would
        # collapse the freshness window and accept replays outside
        # any time bound.
        if window_seconds <= 0:
            msg = "window_seconds must be > 0"
            raise ValueError(msg)
        if max_entries <= 0:
            msg = "max_entries must be > 0"
            raise ValueError(msg)
        self._window = window_seconds
        self._max_entries = max_entries
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._clock: Clock = clock if clock is not None else SystemClock()

    def check_freshness(self, timestamp: float | None) -> bool:
        """Validate timestamp staleness only (no nonce dedup).

        Used by callers that delegate dedup to a durable store (e.g.
        ``IdempotencyService``) but still want to reject stale or
        non-finite timestamps before the durable claim runs.

        Args:
            timestamp: Request timestamp as Unix epoch seconds, or
                ``None`` if no timestamp header was supplied.

        Returns:
            ``True`` if the timestamp is fresh (or absent).
            ``False`` if the timestamp is non-finite or outside the
            configured window.
        """
        if timestamp is None:
            return True
        if not math.isfinite(timestamp):
            logger.warning(
                WEBHOOK_REPLAY_DETECTED,
                reason="non-finite timestamp",
            )
            return False
        # Capture once so the comparison and the logged ``skew`` field
        # cannot disagree if the clock advances between calls.
        now = self._clock.now().timestamp()
        skew = abs(now - timestamp)
        if skew > self._window:
            logger.warning(
                WEBHOOK_REPLAY_DETECTED,
                reason="timestamp outside window",
                skew=skew,
            )
            return False
        return True

    def check(
        self,
        *,
        nonce: str | None,
        timestamp: float | None,
    ) -> bool:
        """Check whether a request is a replay.

        Args:
            nonce: Request nonce (optional).
            timestamp: Request timestamp as Unix epoch seconds.

        Returns:
            ``True`` if the request is safe (not a replay).
            ``False`` if the request should be rejected.
        """
        now = self._clock.now().timestamp()

        # Fail closed: when neither a nonce nor a timestamp is supplied
        # the protector has nothing to check against, so accepting the
        # request would silently downgrade replay protection to a
        # no-op. Reject instead -- misconfigured verifiers or missing
        # headers should surface as rejected deliveries.
        if nonce is None and timestamp is None:
            logger.warning(
                WEBHOOK_REPLAY_DETECTED,
                reason="no freshness signal (nonce and timestamp both missing)",
            )
            return False

        # ``float("nan")`` would bypass the window check because
        # ``abs(now - nan) > window`` evaluates to ``False``. Reject
        # any non-finite timestamp up-front so a malformed header
        # cannot silently pass freshness validation.
        if timestamp is not None and not math.isfinite(timestamp):
            logger.warning(
                WEBHOOK_REPLAY_DETECTED,
                reason="non-finite timestamp",
            )
            return False

        if timestamp is not None and abs(now - timestamp) > self._window:
            logger.warning(
                WEBHOOK_REPLAY_DETECTED,
                reason="timestamp outside window",
                skew=abs(now - timestamp),
            )
            return False

        self._evict(now)

        if nonce is not None:
            # Reject oversized nonces before touching the cache.
            # An attacker who could send arbitrarily long nonces
            # would otherwise be able to make each hash computation
            # increasingly expensive even though the cache entry
            # itself is fixed-size.
            if len(nonce) > MAX_NONCE_CHARS:
                logger.warning(
                    WEBHOOK_REPLAY_DETECTED,
                    reason="nonce exceeds max size",
                    nonce_length=len(nonce),
                    max_nonce_chars=MAX_NONCE_CHARS,
                )
                return False
            # Store a fixed-size SHA-256 digest instead of the raw
            # attacker-controlled string. Bounds per-entry memory
            # independent of nonce length and removes any concern
            # about echoing the nonce back in log output below.
            key = _fingerprint_nonce(nonce)
            if key in self._seen:
                logger.warning(
                    WEBHOOK_REPLAY_DETECTED,
                    reason="duplicate nonce",
                    nonce_fingerprint=key[:16],
                )
                return False
            self._seen[key] = now
            # Bound the store: evict oldest insertion(s) if over limit.
            while len(self._seen) > self._max_entries:
                self._seen.popitem(last=False)

        return True

    def _evict(self, now: float) -> None:
        """Remove nonces older than the window."""
        cutoff = now - self._window
        # OrderedDict preserves insertion order; stop at the first
        # non-expired entry since later insertions are always newer.
        while self._seen:
            nonce, ts = next(iter(self._seen.items()))
            if ts >= cutoff:
                break
            del self._seen[nonce]
