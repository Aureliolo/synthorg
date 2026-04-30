"""Tests for RerankerCache."""

import pytest

from synthorg.memory.retrieval.reranking.cache import RerankerCache
from tests._shared.fake_clock import FakeClock


class TestRerankerCache:
    """Tests for RerankerCache."""

    @pytest.mark.unit
    async def test_get_miss(self) -> None:
        cache = RerankerCache()
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.unit
    async def test_put_and_get(self) -> None:
        cache = RerankerCache()
        await cache.put("key1", ("mem-1", "mem-2"))
        result = await cache.get("key1")
        assert result == ("mem-1", "mem-2")

    @pytest.mark.unit
    async def test_ttl_expiry(self) -> None:
        clock = FakeClock()
        cache = RerankerCache(ttl_seconds=1, clock=clock)
        await cache.put("key1", ("mem-1",))
        clock.advance(2.0)
        assert await cache.get("key1") is None

    @pytest.mark.unit
    async def test_lru_eviction(self) -> None:
        cache = RerankerCache(max_size=2)
        await cache.put("key1", ("mem-1",))
        await cache.put("key2", ("mem-2",))
        assert cache.size == 2
        await cache.put("key3", ("mem-3",))
        assert cache.size == 2
        # key1 should be evicted (oldest)
        result = await cache.get("key1")
        assert result is None

    @pytest.mark.unit
    async def test_invalidate(self) -> None:
        cache = RerankerCache()
        await cache.put("key1", ("mem-1",))
        assert cache.size == 1
        await cache.invalidate("key1")
        assert cache.size == 0
        result = await cache.get("key1")
        assert result is None

    @pytest.mark.unit
    async def test_clear(self) -> None:
        cache = RerankerCache()
        await cache.put("key1", ("mem-1",))
        await cache.put("key2", ("mem-2",))
        assert cache.size == 2
        await cache.clear()
        assert cache.size == 0

    @pytest.mark.unit
    async def test_invalidate_nonexistent(self) -> None:
        cache = RerankerCache()
        await cache.invalidate("nonexistent")
        assert cache.size == 0

    @pytest.mark.unit
    def test_rejects_non_positive_ttl(self) -> None:
        with pytest.raises(ValueError, match="ttl_seconds must be positive"):
            RerankerCache(ttl_seconds=0)

    @pytest.mark.unit
    def test_rejects_non_positive_max_size(self) -> None:
        with pytest.raises(ValueError, match="max_size must be positive"):
            RerankerCache(max_size=0)
