"""Thread -> approval correlation for inbound resume routing.

When an approval is escalated to a chat channel, the notification sink
registers the ``(channel, thread_root_ts) -> approval_id`` mapping here.
An inbound threaded reply then resolves back to the approval it answers,
so the human's reply reaches the parked task. The registry is boot-scoped
transient routing state (not domain state): it mirrors the live socket,
and a still-pending approval is re-notified on its own cadence after a
restart. It is bounded so a long-lived process cannot grow it without
limit.
"""

from collections import OrderedDict
from typing import Final

_DEFAULT_CAPACITY: Final[int] = 4096


def _key(channel: str, thread_ts: str) -> str:
    return f"{channel}\x00{thread_ts}"


class InboundThreadRegistry:
    """Bounded ``(channel, thread_ts) -> approval_id`` correlation map.

    Args:
        capacity: Maximum retained mappings; the oldest is evicted past
            it (LRU-by-insertion), so a busy workspace cannot leak memory.
    """

    __slots__ = ("_capacity", "_entries")

    def __init__(self, *, capacity: int = _DEFAULT_CAPACITY) -> None:
        if capacity <= 0:
            msg = f"capacity must be > 0, got {capacity}"
            raise ValueError(msg)
        self._capacity = capacity
        self._entries: OrderedDict[str, str] = OrderedDict()

    def register(self, *, channel: str, thread_ts: str, approval_id: str) -> None:
        """Record that ``thread_ts`` in ``channel`` answers ``approval_id``."""
        if not channel or not thread_ts or not approval_id:
            return
        key = _key(channel, thread_ts)
        self._entries[key] = approval_id
        self._entries.move_to_end(key)
        while len(self._entries) > self._capacity:
            self._entries.popitem(last=False)

    def resolve(self, *, channel: str, thread_ts: str) -> str | None:
        """Return the approval id a thread reply answers, or ``None``.

        Returns:
            The registered approval id, or ``None`` when the thread is
            not a tracked approval root.
        """
        if not channel or not thread_ts:
            return None
        return self._entries.get(_key(channel, thread_ts))

    def discard(self, *, channel: str, thread_ts: str) -> None:
        """Drop a mapping once its approval is decided (idempotent)."""
        self._entries.pop(_key(channel, thread_ts), None)


__all__ = ["InboundThreadRegistry"]
