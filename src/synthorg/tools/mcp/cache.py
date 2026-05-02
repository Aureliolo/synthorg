"""MCP result cache with TTL and LRU eviction.

Provides an in-memory cache for MCP tool invocation results to
reduce redundant calls to external MCP servers.
"""

import copy
import threading
from collections import OrderedDict
from typing import Any

from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_CACHE_EVICT,
    MCP_CACHE_HIT,
    MCP_CACHE_MISS,
)
from synthorg.observability.metrics_hub import record_cache_operation
from synthorg.tools.base import ToolExecutionResult  # noqa: TC001

_CACHE_NAME = "mcp_result"

logger = get_logger(__name__)


class MCPResultCache:
    """TTL + LRU-bounded cache for MCP tool results.

    Thread-safe via an internal ``threading.Lock`` so concurrent
    threadpool-dispatched tool invocations cannot interleave the
    read-decision-write blocks in :meth:`get` and :meth:`put`.
    Both methods ``copy.deepcopy`` the cached value on the way in and
    out so a caller mutating the returned ``ToolExecutionResult`` --
    e.g. appending to a list field, replacing a dict entry -- cannot
    poison the next cache hit. The deepcopy is intentional, not an
    inefficiency to optimise away.
    Keys are derived from tool name and arguments.

    Args:
        max_size: Maximum number of cached entries.
        ttl_seconds: Time-to-live for cache entries in seconds.
    """

    def __init__(
        self,
        *,
        max_size: int = 256,
        ttl_seconds: float = 60.0,
        clock: Clock | None = None,
    ) -> None:
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._cache: OrderedDict[tuple[str, Any], tuple[float, ToolExecutionResult]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    def get(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult | None:
        """Look up a cached result.

        Returns ``None`` on cache miss or TTL expiry. On hit, the
        entry is moved to the end of the LRU queue.

        Args:
            tool_name: MCP tool name.
            arguments: Tool invocation arguments.

        Returns:
            Cached ``ToolExecutionResult`` or ``None``.
        """
        key = self._make_key(tool_name, arguments)
        hit: ToolExecutionResult | None = None
        miss_reason: str | None = None
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                timestamp, result = entry
                if self._clock.monotonic() - timestamp > self._ttl_seconds:
                    del self._cache[key]
                    miss_reason = "expired"
                else:
                    self._cache.move_to_end(key)
                    hit = result

        if hit is None:
            if miss_reason is None:
                logger.debug(MCP_CACHE_MISS, tool_name=tool_name)
            else:
                logger.debug(
                    MCP_CACHE_MISS,
                    tool_name=tool_name,
                    reason=miss_reason,
                )
            record_cache_operation(cache_name=_CACHE_NAME, outcome="miss")
            return None

        logger.debug(MCP_CACHE_HIT, tool_name=tool_name)
        record_cache_operation(cache_name=_CACHE_NAME, outcome="hit")
        return copy.deepcopy(hit)

    def put(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolExecutionResult,
    ) -> None:
        """Store a result in the cache.

        If the cache is at capacity, the oldest entry is evicted
        before insertion.

        Args:
            tool_name: MCP tool name.
            arguments: Tool invocation arguments.
            result: The ``ToolExecutionResult`` to cache.
        """
        key = self._make_key(tool_name, arguments)
        evicted: list[tuple[str, Any]] = []
        with self._lock:
            # Remove existing entry to refresh position
            if key in self._cache:
                del self._cache[key]

            # Evict oldest if at capacity
            while len(self._cache) >= self._max_size > 0:
                evicted_key, _ = self._cache.popitem(last=False)
                evicted.append(evicted_key)

            if self._max_size > 0:
                self._cache[key] = (self._clock.monotonic(), copy.deepcopy(result))
        for evicted_key in evicted:
            logger.debug(
                MCP_CACHE_EVICT,
                evicted_tool=evicted_key[0],
            )
            record_cache_operation(cache_name=_CACHE_NAME, outcome="evict")

    def invalidate(
        self,
        tool_name: str | None = None,
    ) -> None:
        """Invalidate cache entries.

        Args:
            tool_name: If provided, only invalidate entries for this
                tool. If ``None``, clear all entries.
        """
        with self._lock:
            if tool_name is None:
                self._cache.clear()
                return

            keys_to_remove = [k for k in self._cache if k[0] == tool_name]
            for key in keys_to_remove:
                del self._cache[key]

    @staticmethod
    def _make_key(
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[str, Any]:
        """Build a hashable cache key.

        Args:
            tool_name: MCP tool name.
            arguments: Tool invocation arguments.

        Returns:
            A hashable tuple of (tool_name, frozen_arguments).
        """
        return (tool_name, _make_hashable(arguments))


def _make_hashable(obj: Any) -> Any:
    """Recursively freeze a value into a hashable form.

    Dicts become frozensets of (key, value) tuples, lists and tuples
    become tuples of frozen values, and everything else passes through.

    Args:
        obj: Value to freeze.

    Returns:
        A hashable representation of *obj*.
    """
    if isinstance(obj, dict):
        return frozenset((k, _make_hashable(v)) for k, v in sorted(obj.items()))
    if isinstance(obj, list | tuple):
        return tuple(_make_hashable(item) for item in obj)
    if isinstance(obj, set):
        return frozenset(_make_hashable(item) for item in obj)
    return obj
