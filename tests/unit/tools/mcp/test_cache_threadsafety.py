"""Thread-safety tests for MCPResultCache.

Concurrent ``get`` / ``put`` / ``invalidate`` calls from a thread
pool must not raise (``RuntimeError: dictionary changed size``,
``KeyError`` on the post-delete deepcopy path) and must not lose the
LRU eviction guarantee.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.mcp.cache import MCPResultCache

pytestmark = pytest.mark.unit


class TestMCPResultCacheThreadSafety:
    """Concurrent thread access must remain safe."""

    def test_concurrent_get_put_no_corruption(self) -> None:
        cache = MCPResultCache(max_size=64, ttl_seconds=120.0)
        # Seed a shared key so the reader path actually exercises the
        # locked hit branch (``move_to_end`` + deepcopy) rather than
        # only the miss branch. Without this seed the reader and
        # writer payloads never collide, so the test only stresses
        # concurrent misses + writes.
        shared_args = {"i": -1}
        cache.put("shared-tool", shared_args, ToolExecutionResult(content="seed"))

        def writer(i: int) -> None:
            cache.put("shared-tool", shared_args, ToolExecutionResult(content=str(i)))

        def reader(i: int) -> None:
            del i
            # Reader hits the seeded entry on every iteration, exercising
            # the get -> move_to_end -> deepcopy path under contention.
            cache.get("shared-tool", shared_args)

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = []
            for i in range(200):
                fn = writer if i % 2 == 0 else reader
                futures.append(pool.submit(fn, i))
            for f in futures:
                f.result()

    def test_concurrent_invalidate_does_not_raise(self) -> None:
        cache = MCPResultCache(max_size=64, ttl_seconds=120.0)
        for i in range(32):
            cache.put(f"tool-{i % 4}", {"i": i}, ToolExecutionResult(content=str(i)))

        def writer(i: int) -> None:
            cache.put(f"tool-{i % 4}", {"i": i}, ToolExecutionResult(content=str(i)))

        def invalidator(i: int) -> None:
            del i
            cache.invalidate(tool_name="tool-0")

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(invalidator if i % 5 == 0 else writer, i)
                for i in range(120)
            ]
            for f in futures:
                f.result()

    def test_eviction_under_concurrency_keeps_max_size(self) -> None:
        cache = MCPResultCache(max_size=8, ttl_seconds=120.0)

        def writer(i: int) -> None:
            cache.put(f"tool-{i}", {}, ToolExecutionResult(content=str(i)))

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(writer, i) for i in range(64)]
            for f in futures:
                f.result()
        # Internal cache must stay bounded.
        assert len(cache._cache) <= 8
