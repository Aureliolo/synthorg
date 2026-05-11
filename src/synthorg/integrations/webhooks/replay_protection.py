"""Webhook replay protection.

Prevents replay attacks by tracking nonces and validating
timestamps within a configurable window.

The mutating ``check`` method holds a ``threading.Lock`` so concurrent
threadpool-dispatched webhook handlers cannot both pass the nonce
duplicate test and insert the same nonce.  Without the lock, two
identical webhook deliveries arriving simultaneously could each see
the nonce as fresh and both proceed, losing the replay-protection
guarantee.
"""

import hashlib
import math
import threading
from collections import OrderedDict
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import WEBHOOK_REPLAY_DETECTED

logger = get_logger(__name__)

_DEFAULT_WINDOW_SECONDS: Final[int] = 300
_DEFAULT_MAX_ENTRIES: Final[int] = 10_000
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
        self._lock = threading.Lock()

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
        return self._check_freshness_at(timestamp, self._clock.now().timestamp())

    def _check_freshness_at(self, timestamp: float | None, now: float) -> bool:
        """Validate timestamp staleness against a caller-supplied *now*.

        Allows :meth:`check` to sample the clock exactly once and pass
        the same snapshot to both the freshness check and the nonce
        eviction so a clock advance between two reads cannot open a
        boundary replay window where the freshness check uses one
        ``now`` and the nonce eviction uses another.
        """
        if timestamp is None:
            return True
        if not math.isfinite(timestamp):
            logger.warning(
                WEBHOOK_REPLAY_DETECTED,
                reason="non-finite timestamp",
            )
            return False
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

        Delegates timestamp freshness to :meth:`check_freshness` and
        nonce dedup to :meth:`_check_nonce` so each concern stays
        isolated and the function body fits comfortably under the
        50-line limit.

        Args:
            nonce: Request nonce (optional).
            timestamp: Request timestamp as Unix epoch seconds.

        Returns:
            ``True`` if the request is safe (not a replay).
            ``False`` if the request should be rejected.
        """
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
        # Sample the clock once per ``check()`` call and reuse the
        # snapshot for both freshness and nonce-eviction decisions so
        # a clock advance mid-call cannot open a boundary replay
        # window where the freshness check observes one ``now`` and
        # the nonce eviction observes another.
        now = self._clock.now().timestamp()
        if not self._check_freshness_at(timestamp, now):
            return False
        return self._check_nonce(nonce=nonce, now=now)

    def _check_nonce(self, *, nonce: str | None, now: float) -> bool:
        """Validate the nonce dedup window.

        Caller is responsible for freshness checks; this method only
        handles the nonce side of replay protection. ``now`` is taken
        from the same clock read the caller used so eviction and
        dedup observe the same instant.
        """
        if nonce is None:
            with self._lock:
                self._evict_locked(now)
            return True

        # Reject oversized nonces before touching the cache. An
        # attacker who could send arbitrarily long nonces would
        # otherwise be able to make each hash computation
        # increasingly expensive even though the cache entry itself
        # is fixed-size.
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
        # independent of nonce length and removes any concern about
        # echoing the nonce back in log output below.
        key = _fingerprint_nonce(nonce)
        with self._lock:
            self._evict_locked(now)
            duplicate = key in self._seen
            if not duplicate:
                self._seen[key] = now
                # Bound the store: evict oldest insertion(s) if over limit.
                while len(self._seen) > self._max_entries:
                    self._seen.popitem(last=False)
        if duplicate:
            logger.warning(
                WEBHOOK_REPLAY_DETECTED,
                reason="duplicate nonce",
                nonce_fingerprint=key[:16],
            )
            return False
        return True

    def _evict_locked(self, now: float) -> None:
        """Remove nonces older than the window.

        Caller must hold ``self._lock``. Walks every entry instead of
        early-exiting on the first non-expired one: the caller in
        ``check()`` samples ``now`` BEFORE acquiring the lock, which
        means under contention the insertion order in ``self._seen``
        no longer matches timestamp order (a thread that read an
        older ``now`` can win the lock after a thread with a newer
        ``now`` already inserted, leaving an older timestamp behind a
        newer one in the ordered map). Walking all entries keeps the
        duplicate-reject window pinned to ``self._window`` even when
        that ordering invariant is broken. The walk is O(n) but
        bounded by ``self._max_entries``.
        """
        cutoff = now - self._window
        expired = [nonce for nonce, ts in self._seen.items() if ts < cutoff]
        for nonce in expired:
            del self._seen[nonce]
