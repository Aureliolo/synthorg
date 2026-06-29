# module-kind: code
"""Per-session publish bookkeeping for the event-stream hub.

Extracted from ``stream.py`` to keep the hub under its module-size
budget. Owns two bounded per-session structures, both consulted under the
hub's publish lock:

* a replay ring buffer (recent events per session) for SSE
  ``Last-Event-ID`` reconnect replay, and
* a dedup window (``event.id`` -> monotonic-seen-at) so a retried publish
  does not double-emit to subscribers.

All methods assume the caller already holds the hub lock; the ledger does
no locking of its own.
"""

import asyncio
from collections import OrderedDict, deque
from collections.abc import Mapping
from types import MappingProxyType

from synthorg.communication.event_stream.types import StreamEvent
from synthorg.observability import get_logger
from synthorg.observability.events.event_stream import EVENT_STREAM_HUB_PUBLISH_FAILED

logger = get_logger(__name__)


class PublishLedger:
    """Bounded replay-history + publish-dedup state, keyed by session."""

    __slots__ = (
        "_dedup_max_entries_per_session",
        "_dedup_ttl_seconds",
        "_history",
        "_history_max_sessions",
        "_history_per_session",
        "_seen",
    )

    def __init__(
        self,
        *,
        history_max_sessions: int,
        history_per_session: int,
        dedup_ttl_seconds: float,
        dedup_max_entries_per_session: int,
    ) -> None:
        # Fail-fast on bad bounds; otherwise the trim loop in
        # ``record_published`` would ``popitem`` an empty OrderedDict and a
        # negative TTL would short-circuit eviction into always-stale.
        if dedup_ttl_seconds < 0:
            msg = f"dedup_ttl_seconds must be >= 0, got {dedup_ttl_seconds}"
            raise ValueError(msg)
        if dedup_max_entries_per_session < 1:
            msg = (
                "dedup_max_entries_per_session must be >= 1, got "
                f"{dedup_max_entries_per_session}"
            )
            raise ValueError(msg)
        if history_per_session < 1:
            msg = f"history_per_session must be >= 1, got {history_per_session}"
            raise ValueError(msg)
        if history_max_sessions < 1:
            msg = f"history_max_sessions must be >= 1, got {history_max_sessions}"
            raise ValueError(msg)
        self._history_max_sessions = history_max_sessions
        self._history_per_session = history_per_session
        self._dedup_ttl_seconds = dedup_ttl_seconds
        self._dedup_max_entries_per_session = dedup_max_entries_per_session
        # Recency-ordered (``move_to_end`` on every re-publish) so the
        # session-cap eviction drops the least-recently-published session.
        self._history: OrderedDict[str, deque[StreamEvent]] = OrderedDict()
        # Per-session ``event.id`` -> ``monotonic_seen_at``.
        self._seen: dict[str, OrderedDict[str, float]] = {}

    @property
    def dedup_ttl_seconds(self) -> float:
        """The configured dedup TTL (read by the hub for its publish log)."""
        return self._dedup_ttl_seconds

    def set_history_max_sessions(self, value: int) -> None:
        """Update the LRU session cap (hot).

        The next ``record_history`` of a new session evicts down to the new
        cap; the change applies without a restart.

        Raises:
            ValueError: If *value* is below 1 (an empty-cap ``popitem``
                would break the eviction loop).
        """
        if value < 1:
            msg = f"history_max_sessions must be >= 1, got {value}"
            raise ValueError(msg)
        self._history_max_sessions = value

    def set_history_per_session(self, value: int) -> None:
        """Update the per-session replay ring-buffer depth (hot).

        Sessions created after this call size their ``deque`` to the new
        depth; existing per-session buffers keep their ``maxlen`` until the
        session is recycled (accepted behaviour -- ``deque.maxlen`` is
        immutable). The change applies without a restart.

        Raises:
            ValueError: If *value* is below 1.
        """
        if value < 1:
            msg = f"history_per_session must be >= 1, got {value}"
            raise ValueError(msg)
        self._history_per_session = value

    @property
    def seen_event_ids(self) -> Mapping[str, OrderedDict[str, float]]:
        """Read-only view of the per-session dedup windows.

        Exposed for introspection (tests, diagnostics) only. Mutation runs
        solely through ``record_published`` / ``forget_session``; the
        idle-subscriber janitor calls ``forget_session`` rather than poking
        this map, so the dedup windows stay encapsulated.
        """
        return MappingProxyType(self._seen)

    def forget_session(self, session_id: str) -> None:
        """Drop a session's dedup window (history is retained for replay)."""
        self._seen.pop(session_id, None)

    def record_history(self, event: StreamEvent) -> None:
        """Append *event* to its session's replay ring buffer.

        Bounds memory two ways: each session's ``deque`` has a fixed
        ``maxlen`` (oldest event evicted) and the number of tracked sessions
        is LRU-capped (least-recently-published session evicted). A
        re-publish of an event id already in the buffer is skipped so a
        reconnecting client never replays the same event twice.
        """
        hist = self._history.get(event.session_id)
        if hist is None:
            while len(self._history) >= self._history_max_sessions:
                self._history.popitem(last=False)
            hist = deque(maxlen=self._history_per_session)
            self._history[event.session_id] = hist
        else:
            self._history.move_to_end(event.session_id)
            # Full-buffer membership check, NOT just the adjacent tail: the
            # no-subscriber publish path clears the dedup window via
            # ``forget_session``, so a retried event arriving after other
            # events would otherwise be appended twice and replayed twice on
            # reconnect. The deque is bounded (``history_per_session``), so
            # the scan is O(n) over a small ring under the hub publish lock.
            if any(existing.id == event.id for existing in hist):
                return
        hist.append(event)

    def replay_history(
        self,
        session_id: str,
        after_id: str,
        queue: asyncio.Queue[StreamEvent],
    ) -> int:
        """Drain retained events published after *after_id* into *queue*.

        Replays every retained event strictly after the one matching
        ``after_id``; when ``after_id`` is not in the buffer (too old /
        unknown) the full retained buffer is replayed so the client is not
        silently left with a gap. Stops on ``QueueFull`` so a history larger
        than the subscriber queue cannot block the subscribe call.

        Returns:
            The number of events replayed into the queue.
        """
        hist = self._history.get(session_id)
        if not hist:
            return 0
        events = list(hist)
        start = 0
        for index, recorded in enumerate(events):
            if recorded.id == after_id:
                start = index + 1
                break
        pending = events[start:]
        replayed = 0
        for recorded in pending:
            try:
                queue.put_nowait(recorded)
            except asyncio.QueueFull:
                logger.warning(
                    EVENT_STREAM_HUB_PUBLISH_FAILED,
                    session_id=session_id,
                    event_id=recorded.id,
                    note="Replay queue full; remaining history dropped",
                    events_replayed=replayed,
                    events_skipped=len(pending) - replayed,
                )
                break
            replayed += 1
        return replayed

    def is_duplicate(self, event: StreamEvent, now: float) -> bool:
        """Return ``True`` if *event* was already published within the TTL.

        Evicts expired entries from the per-session window before testing
        membership so a stale entry does not falsely register as a duplicate.
        """
        seen = self._seen.get(event.session_id)
        if seen is None:
            return False
        # ``dedup_ttl_seconds == 0`` is the documented "disable time-based
        # eviction" knob: entries fall out only via the per-session size
        # bound. Without this guard the cutoff would equal ``now`` and every
        # previously recorded entry would test as expired, draining the
        # window on every publish and silently disabling deduplication.
        if self._dedup_ttl_seconds > 0:
            cutoff = now - self._dedup_ttl_seconds
            while seen:
                oldest_id, oldest_ts = next(iter(seen.items()))
                if oldest_ts >= cutoff:
                    break
                del seen[oldest_id]
        if not seen:
            del self._seen[event.session_id]
            return False
        return event.id in seen

    def record_published(self, event: StreamEvent, now: float) -> None:
        """Record *event* as published in the per-session dedup window.

        Bounds each session's window by ``dedup_max_entries_per_session`` so
        a single noisy session cannot exhaust memory.
        """
        seen = self._seen.setdefault(event.session_id, OrderedDict())
        seen[event.id] = now
        while len(seen) > self._dedup_max_entries_per_session:
            seen.popitem(last=False)
