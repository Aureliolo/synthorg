# module-kind: code
"""Idle-subscriber reclamation for :class:`EventStreamHub`.

The hub spawns :func:`janitor_loop` as a background task; it periodically
calls :func:`prune_idle_subscribers` to drop subscribers whose last
activity is older than the configured TTL, so a client that disconnects
without unsubscribing does not pin its queue forever. Kept beside the
hub as free functions operating on the hub's subscriber maps, so the
fan-out hot path in ``stream.py`` stays focused on publish/subscribe.
"""

import asyncio
from collections import OrderedDict
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field

from synthorg.communication.event_stream.types import StreamEvent
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.event_stream import (
    EVENT_STREAM_HUB_JANITOR_FAILED,
    EVENT_STREAM_HUB_JANITOR_PRUNED,
)

logger = get_logger(__name__)


# Intentionally NOT frozen: ``last_active`` is mutated in-place under the
# hub's lock. CLAUDE.md "Frozen by default" deviation is justified
# because allocating a fresh ``_Subscriber`` on every successful publish
# would churn the hot fan-out path.
@dataclass(slots=True)
class _Subscriber:
    """Per-subscriber bookkeeping owned by ``EventStreamHub``.

    ``last_active`` carries the monotonic timestamp of the most recent
    activity (subscribe call or successful publish to this subscriber).
    The janitor reads ``last_active`` to evict idle subscribers; the
    field is mutated in-place under the hub's lock so all reads / writes
    happen-before each other through the lock.
    """

    queue: asyncio.Queue[StreamEvent] = field()
    last_active: float = field()


async def prune_idle_subscribers(
    *,
    clock: Clock,
    idle_ttl_seconds: float,
    subscribers: dict[str, list[_Subscriber]],
    seen_event_ids: dict[str, OrderedDict[str, float]],
    lock: asyncio.Lock,
) -> None:
    """Drop subscribers whose ``last_active`` is older than the TTL.

    Args:
        clock: Monotonic-time source.
        idle_ttl_seconds: Inactivity window before a subscriber is evicted.
        subscribers: The hub's per-session subscriber lists (mutated in
            place under ``lock``).
        seen_event_ids: The hub's per-session dedup windows (pruned in
            lockstep when a session empties).
        lock: The hub's current-loop lock guarding the maps.
    """
    now = clock.monotonic()
    cutoff = now - idle_ttl_seconds
    pruned = 0
    async with lock:
        for session_id in list(subscribers):
            kept = [sub for sub in subscribers[session_id] if sub.last_active >= cutoff]
            pruned += len(subscribers[session_id]) - len(kept)
            if kept:
                subscribers[session_id] = kept
            else:
                del subscribers[session_id]
                seen_event_ids.pop(session_id, None)
    if pruned > 0:
        logger.info(
            EVENT_STREAM_HUB_JANITOR_PRUNED,
            pruned_subscribers=pruned,
            remaining_sessions=len(subscribers),
            idle_ttl_seconds=idle_ttl_seconds,
        )


async def janitor_loop(
    *,
    clock: Clock,
    janitor_interval_seconds: float,
    prune: Callable[[], Coroutine[object, object, None]],
) -> None:
    """Periodically run ``prune`` until cancelled.

    A prune failure (lock acquisition error, clock failure, dict-mutation
    race) must not kill the loop -- otherwise the hub silently stops
    reclaiming memory and the original leak the janitor was added to fix
    returns. Re-raise only the system-level errors (``CancelledError``,
    ``MemoryError``, ``RecursionError``); log every other exception and
    continue.

    Args:
        clock: Sleep source between sweeps.
        janitor_interval_seconds: Delay between sweeps.
        prune: Zero-argument coroutine that performs one sweep.

    Raises:
        asyncio.CancelledError: Propagated on shutdown so the janitor
            task stops cleanly.
    """
    # lint-allow: long-running-loop-kill-switch -- stop()/cancel drives shutdown.
    while True:
        await clock.sleep(janitor_interval_seconds)
        try:
            await prune()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                EVENT_STREAM_HUB_JANITOR_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
